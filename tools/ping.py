"""Try a MODEL_ID before committing to it.

  python tools/ping.py --find deepseek            # list matching model ids + supported params (no key needed)
  python tools/ping.py --model deepseek/xxx       # one tiny schema-constrained call, prints usage
  python tools/ping.py --model deepseek/xxx --reasoning low --json-mode object

Needs OPENROUTER_API_KEY in the environment for calls. Never prints the key.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from p2p.budget import Budget  # noqa: E402
from p2p.config import settings_for  # noqa: E402
from p2p.llm import LLMError, chat  # noqa: E402
from p2p.trace import Trace  # noqa: E402

SCHEMA = {
    "type": "object",
    "properties": {"sum": {"type": "number"}, "word": {"type": "string"}},
    "required": ["sum", "word"],
    "additionalProperties": False,
}
REASONING = {
    "config": "config", "none": None, "off": {"enabled": False, "exclude": True},
    "low": {"effort": "low", "exclude": True}, "medium": {"effort": "medium", "exclude": True},
}


def find(query):
    r = requests.get("https://openrouter.ai/api/v1/models", timeout=30)
    r.raise_for_status()
    for m in r.json()["data"]:
        if query.lower() in (m["id"] + " " + m.get("name", "")).lower():
            p = m.get("pricing", {})
            sp = set(m.get("supported_parameters") or [])
            flags = [f for f in ("structured_outputs", "response_format", "reasoning", "include_reasoning") if f in sp]
            print(f"{m['id']:<50} ctx={m.get('context_length')} in=${p.get('prompt')} out=${p.get('completion')} {flags}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--find")
    ap.add_argument("--model")
    ap.add_argument("--reasoning", choices=list(REASONING), default="config")
    ap.add_argument("--json-mode", choices=["schema", "object", "none"])
    ap.add_argument("--max-tokens", type=int, default=300)
    a = ap.parse_args()
    if a.find:
        find(a.find)
        return 0
    if not a.model:
        ap.error("--model or --find is required")

    cfg = settings_for(a.model)
    if a.reasoning != "config":
        cfg["reasoning"] = REASONING[a.reasoning]
    if a.json_mode:
        cfg["json_mode"] = a.json_mode
    print("settings:", json.dumps(cfg))
    out = Path("runs/ping")
    trace, budget = Trace(out), Budget()
    msgs = [{"role": "system", "content": "Reply with JSON only: {\"sum\": number, \"word\": string}."},
            {"role": "user", "content": "Add 17 and 25. Put the result in sum and the English word for it in word."}]
    t = time.monotonic()
    try:
        content, usage = chat(msgs, model=a.model, max_tokens=a.max_tokens, purpose="ping", schema=SCHEMA,
                              budget=budget, trace=trace, cfg=cfg)
        print("content:", content, "| correct:", content.get("sum") == 42)
        rc = 0
    except LLMError as e:
        print(f"FAILED: {type(e).__name__}: {e}")
        rc = 1
    print(f"elapsed: {time.monotonic() - t:.2f}s")
    last = [json.loads(line) for line in (out / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    for ev in last:
        if ev["action"] == "llm_call":
            keys = ("attempt", "http_status", "finish_reason", "provider", "prompt_tokens", "completion_tokens",
                    "reasoning_tokens", "cached_tokens", "total_tokens", "elapsed_s", "error")
            print({k: ev.get(k) for k in keys})
    trace.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
