"""Append-only JSONL trace. One event per line, flushed at once. Never logs secrets or message text."""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

RESULTS = {"ok", "fail", "skip", "info"}
SECRET_KEYS = {"authorization", "api_key", "apikey", "openrouter_api_key", "headers"}
# Keys whose values may hold prompt/response text or hidden reasoning. Dropped, never logged.
TEXT_KEYS = {"messages", "content", "prompt", "completion", "reasoning", "reasoning_details", "body", "response"}
SECRET_MARK = "sk-or-"


def fingerprint(text: str) -> dict:
    """Safe stand-in for text: its length and a short sha256 prefix."""
    data = (text or "").encode("utf-8")
    return {"chars": len(text or ""), "sha256": hashlib.sha256(data).hexdigest()[:12]}


def redact(obj):
    """Return a copy with secrets and message/reasoning text removed."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            kl = str(k).lower()
            if kl in SECRET_KEYS or kl in TEXT_KEYS:
                continue
            out[k] = redact(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact(v) for v in obj]
    if isinstance(obj, str) and SECRET_MARK in obj:
        return "[REDACTED]"
    return obj


class Trace:
    def __init__(self, out_dir: str | Path, t0: float | None = None):
        self.t0 = time.monotonic() if t0 is None else t0
        self.path = Path(out_dir) / "trace.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(self.path, "w", encoding="utf-8", newline="\n")
        self.calls = 0
        self.checks_passed = 0
        self.checks_failed = 0
        self.revisions = 0

    def elapsed(self) -> float:
        return round(time.monotonic() - self.t0, 3)

    def event(self, stage: str, action: str, result: str = "info", **extra) -> dict:
        if result not in RESULTS:
            result = "info"
        ev = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "t": self.elapsed(),
            "stage": stage,
            "action": action,
            "result": result,
        }
        ev.update(redact(extra))
        self._f.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
        self._f.flush()
        return ev

    def llm_call(self, *, stage: str, call_index: int, purpose: str, model: str, ok: bool,
                 prompt_tokens=None, completion_tokens=None, reasoning_tokens=None,
                 cached_tokens=None, total_tokens=None, elapsed_s=None, finish_reason=None,
                 generation_id=None, http_status=None, attempt=1, retry_reason=None,
                 prompt_text: str | None = None, **extra) -> dict:
        self.calls += 1
        fields = dict(
            call_index=call_index, purpose=purpose, model=model,
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            reasoning_tokens=reasoning_tokens, cached_tokens=cached_tokens,
            total_tokens=total_tokens, elapsed_s=elapsed_s, finish_reason=finish_reason,
            generation_id=generation_id, http_status=http_status, attempt=attempt,
            retry_reason=retry_reason,
        )
        if prompt_text is not None:
            fields["prompt_fp"] = fingerprint(prompt_text)
        fields.update(extra)
        return self.event(stage, "llm_call", "ok" if ok else "fail", **fields)

    def check(self, name: str, passed: bool, detail=None, stage: str = "check", skipped: bool = False, **extra) -> dict:
        if skipped:
            return self.event(stage, f"check:{name}", "skip", detail=detail, **extra)
        if passed:
            self.checks_passed += 1
        else:
            self.checks_failed += 1
        return self.event(stage, f"check:{name}", "ok" if passed else "fail", detail=detail, **extra)

    def revision(self, round: int, targets, reason: str, stage: str = "repair") -> dict:
        self.revisions += 1
        return self.event(stage, "revision", "info", round=round, targets=list(targets), reason=reason)

    def summary(self, totals: dict, ok: bool, exit_code: int) -> dict:
        return self.event("summary", "finish", "ok" if ok else "fail", exit_code=exit_code,
                          elapsed_s=self.elapsed(), checks_passed=self.checks_passed,
                          checks_failed=self.checks_failed, revisions=self.revisions, **totals)

    def close(self):
        if not self._f.closed:
            self._f.close()
