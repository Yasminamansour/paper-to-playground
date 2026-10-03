"""Short summary of one or more runs: calls, tokens, time, checks.

  python tools/report.py runs/a runs/b
"""
import json
import sys
from pathlib import Path


def report(run: Path):
    lines = [json.loads(l) for l in (run / "trace.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"== {run}")
    for e in lines:
        if e["action"] == "llm_call":
            print(f"  call {e.get('purpose'):<14} {e['result']:<4} prompt={e.get('prompt_tokens')} "
                  f"completion={e.get('completion_tokens')} reasoning={e.get('reasoning_tokens')} "
                  f"{e.get('elapsed_s')}s finish={e.get('finish_reason')} {e.get('error') or ''}")
        elif e["action"].startswith("check:"):
            mark = {"ok": "PASS", "fail": "FAIL", "skip": "skip"}.get(e["result"], e["result"])
            detail = e.get("detail")
            print(f"  {mark:<4} {e['action'][6:]:<34} {'' if detail in (None, 'ok') else json.dumps(detail, ensure_ascii=False)[:150]}")
        elif e["action"] in ("revision", "repair_result") or e["result"] == "fail":
            print(f"  {e['action']}: {e.get('reason') or e.get('error') or e.get('detail') or e['result']}")
    s = lines[-1]
    if s.get("stage") == "summary":
        print(f"  TOTAL exit={s['exit_code']} calls={s.get('calls')} tokens={s.get('total_tokens')} "
              f"(prompt {s.get('prompt_tokens')}, completion {s.get('completion_tokens')}) time={s.get('elapsed_s')}s")


if __name__ == "__main__":
    for arg in sys.argv[1:] or ["out"]:
        report(Path(arg))
