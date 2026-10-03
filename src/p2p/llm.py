"""OpenRouter chat client: one HTTP attempt = one budgeted call = one trace line.

Never logs the key, message text or reasoning. Retries only 408/429/502/503 and network
timeouts, at most 2 times. Raises typed errors so callers can decide what to do.
"""
from __future__ import annotations

import json
import os
import re
import time

import requests

from .config import settings_for

API_URL = "https://openrouter.ai/api/v1/chat/completions"
RETRY_STATUS = {408, 429, 502, 503}
MAX_RETRIES = 2
BACKOFF = (2.0, 5.0)
RETRY_AFTER_CAP = 20.0
STARVED_MARGIN = 50  # visible tokens below this on finish_reason=length means reasoning ate the budget


class LLMError(Exception):
    """Call failed and should not be retried by the caller."""


class MissingKey(LLMError):
    pass


class BudgetExhausted(LLMError):
    pass


class Truncated(LLMError):
    def __init__(self, msg, text="", usage=None):
        super().__init__(msg)
        self.text, self.usage = text, usage or {}


class ReasoningStarved(Truncated):
    pass


class BadJSON(LLMError):
    def __init__(self, msg, text=""):
        super().__init__(msg)
        self.text = text


def norm_usage(u: dict | None) -> dict:
    """Flatten OpenRouter usage into the fields we trace. Missing values stay None (never invented)."""
    if not u:
        return {"usage_missing": True}
    ctd = u.get("completion_tokens_details") or {}
    ptd = u.get("prompt_tokens_details") or {}
    return {
        "prompt_tokens": u.get("prompt_tokens"),
        "completion_tokens": u.get("completion_tokens"),
        "reasoning_tokens": ctd.get("reasoning_tokens", 0),
        "cached_tokens": ptd.get("cached_tokens", 0),
        "total_tokens": u.get("total_tokens"),
    }


_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.I)


def parse_json(text: str):
    t = _FENCE.sub("", text or "").strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    a, b = t.find("{"), t.rfind("}")
    if a != -1 and b > a:
        try:
            return json.loads(t[a:b + 1])
        except json.JSONDecodeError:
            pass
    raise BadJSON("model output is not valid JSON", text or "")


def _safe_msg(s) -> str:
    s = str(s or "")[:300]
    return "[REDACTED]" if "sk-or-" in s else s


def _retry_after(headers) -> float | None:
    try:
        v = float((headers or {}).get("Retry-After", ""))
        return max(0.0, min(v, RETRY_AFTER_CAP))
    except (TypeError, ValueError):
        return None


def build_body(messages, *, model, max_tokens, schema, purpose, cfg) -> dict:
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": cfg.get("temperature", 0.2),
        "stream": False,
    }
    if cfg.get("reasoning") is not None:
        body["reasoning"] = cfg["reasoning"]
    if schema is not None:
        mode = cfg.get("json_mode", "schema")
        if mode == "schema":
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": purpose, "strict": True, "schema": schema}}
        elif mode == "object":
            body["response_format"] = {"type": "json_object"}
    if cfg.get("require_params"):
        body["provider"] = {"require_parameters": True}
    return body


def chat(messages, *, model, max_tokens, purpose, budget, trace, schema=None, stage=None,
         session=None, sleep=time.sleep, cfg=None):
    """Send one logical request (with bounded retries). Returns (content, usage).
    content is a parsed object when schema is given, else a string."""
    stage = stage or purpose
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        trace.event(stage, "llm_call", "fail", purpose=purpose, error="OPENROUTER_API_KEY is not set")
        raise MissingKey("OPENROUTER_API_KEY is not set")
    cfg = cfg or settings_for(model)
    http = session or requests
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
               "X-Title": "paper-to-playground"}
    prompt_text = "\n".join(str(m.get("content", "")) for m in messages)

    retry_reason = None
    for attempt in range(1, MAX_RETRIES + 2):
        if not budget.can_call(max_tokens):
            trace.event(stage, "llm_call", "skip", purpose=purpose, reason="budget exhausted",
                        **budget.totals())
            raise BudgetExhausted(f"budget exhausted before {purpose}")
        mt = budget.clamp_max_tokens(max_tokens)
        timeout = min(150.0, budget.time_left() - 15.0)
        if timeout < 10:
            trace.event(stage, "llm_call", "skip", purpose=purpose, reason="no time left")
            raise BudgetExhausted("no time left for another call")
        body = build_body(messages, model=model, max_tokens=mt, schema=schema, purpose=purpose, cfg=cfg)

        t = time.monotonic()
        status, data, resp_headers, err = None, None, {}, None
        try:
            r = http.post(API_URL, headers=headers, json=body, timeout=timeout)
            status, resp_headers = r.status_code, getattr(r, "headers", {}) or {}
            try:
                data = r.json()
            except ValueError:
                data = None
        except requests.Timeout:
            err = "timeout"
        except requests.ConnectionError:
            err = "connection_error"
        elapsed = round(time.monotonic() - t, 3)

        # Work out what happened.
        api_err = None
        if isinstance(data, dict):
            api_err = data.get("error")
            ch = (data.get("choices") or [{}])[0]
            if not api_err and isinstance(ch, dict) and ch.get("error"):
                api_err = ch["error"]
        code = status
        if isinstance(api_err, dict) and isinstance(api_err.get("code"), int):
            code = api_err["code"]
        usage = data.get("usage") if isinstance(data, dict) else None

        # Count the attempt. With no usage after a timeout, charge max_tokens (worst case).
        budget.record(usage, requested_max_tokens=mt if (usage is None and err == "timeout") else None)
        ok = err is None and status == 200 and not api_err and isinstance(data, dict)
        choice = (data.get("choices") or [{}])[0] if ok else {}
        finish = choice.get("finish_reason") if ok else None
        trace.llm_call(stage=stage, call_index=budget.calls, purpose=purpose, model=model, ok=ok,
                       elapsed_s=elapsed, finish_reason=finish,
                       generation_id=data.get("id") if isinstance(data, dict) else None,
                       http_status=status, attempt=attempt, retry_reason=retry_reason,
                       max_tokens=mt, prompt_text=prompt_text,
                       provider=data.get("provider") if isinstance(data, dict) else None,
                       error=err or (_safe_msg(api_err.get("message") if isinstance(api_err, dict) else api_err)
                                     if api_err else None),
                       **norm_usage(usage))

        if ok:
            break
        retryable = err is not None or code in RETRY_STATUS
        if not retryable or attempt > MAX_RETRIES:
            what = err or f"HTTP {code}"
            msg = api_err.get("message") if isinstance(api_err, dict) else api_err
            raise LLMError(f"{purpose}: {what} {_safe_msg(msg)}".strip())
        wait = _retry_after(resp_headers)
        wait = BACKOFF[attempt - 1] if wait is None else wait
        wait = min(wait, max(0.0, budget.time_left() - 30))
        retry_reason = err or f"http_{code}"
        sleep(wait)

    msg = choice.get("message") or {}
    content = msg.get("content") or ""
    u = norm_usage(usage)
    if finish == "length":
        visible = (u.get("completion_tokens") or 0) - (u.get("reasoning_tokens") or 0)
        if visible < STARVED_MARGIN:
            raise ReasoningStarved(f"{purpose}: reasoning used the token budget", content, u)
        raise Truncated(f"{purpose}: output cut at max_tokens", content, u)
    if schema is not None:
        return parse_json(content), u
    return content, u
