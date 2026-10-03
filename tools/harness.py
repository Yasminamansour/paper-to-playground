"""Run every case like the grader does: twice each, fresh output folders, same limits.

  python tools/harness.py --model deepseek/deepseek-v4.1-flash
  python tools/harness.py --model ... --cases a_attention c_softmax_temperature --runs 1 --jobs 3

Prints a table and saves runs/harness/summary.json. Flags runs that break a limit or a target.
The agent runs as a subprocess with a clean environment (PATH, key, and Windows basics only).
"""
import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from offline_scan import scan  # noqa: E402

KEEP_ENV = ("PATH", "OPENROUTER_API_KEY", "SYSTEMROOT", "TEMP", "TMP", "USERPROFILE", "HOME", "P2P_DEBUG")
LIMITS = {"calls": 8, "tokens": 20_000, "seconds": 300}


def run_one(case: Path, k: int, model: str, base: Path) -> dict:
    out = base / case.stem / f"run_{k}"
    if out.exists():
        for f in out.rglob("*"):
            if f.is_file():
                f.unlink()
    env = {v: os.environ[v] for v in KEEP_ENV if v in os.environ}
    t = time.monotonic()
    try:
        p = subprocess.run([sys.executable, str(ROOT / "agent.py"), "--input", str(case), "--output", str(out),
                            "--model", model], env=env, capture_output=True, text=True, timeout=600)
        code, err = p.returncode, p.stderr.strip()[-300:]
    except subprocess.TimeoutExpired:
        code, err = "timeout", "killed after 600 s"
    wall = round(time.monotonic() - t, 1)
    row = {"case": case.stem, "run": k, "exit": code, "wall_s": wall, "error": err}
    trace = out / "trace.jsonl"
    events = [json.loads(l) for l in trace.read_text(encoding="utf-8").splitlines() if l.strip()] if trace.exists() else []
    summ = events[-1] if events and events[-1].get("stage") == "summary" else {}
    calls = [e for e in events if e.get("action") == "llm_call"]
    written = [e for e in events if e.get("action") == "write_page"]
    row.update({
        "calls": summ.get("calls"), "prompt": summ.get("prompt_tokens"), "completion": summ.get("completion_tokens"),
        "tokens": summ.get("total_tokens"), "revisions": summ.get("revisions"),
        "providers": sorted({e.get("provider") for e in calls if e.get("provider")}),
        "per_call": [(e.get("purpose"), e.get("provider"), e.get("prompt_tokens"), e.get("completion_tokens"),
                      e.get("elapsed_s")) for e in calls],
        "failed": written[-1].get("failed") if written else ["no page"],
        "critical": written[-1].get("critical") if written else 1,
        "version": written[-1].get("version") if written else None,
    })
    page = out / "index.html"
    row["kb"] = round(page.stat().st_size / 1024, 1) if page.exists() else 0
    row["offline_problems"] = scan(page.read_text(encoding="utf-8"), _source_url(case)) if page.exists() else ["no page"]
    flags = []
    if code != 0:
        flags.append(f"exit {code}")
    if (row["calls"] or 0) > LIMITS["calls"]:
        flags.append("calls>8")
    if (row["tokens"] or 0) > LIMITS["tokens"]:
        flags.append("tokens>20k")
    if wall > LIMITS["seconds"]:
        flags.append("time>300s")
    if row["critical"]:
        flags.append("critical failure")
    if row["offline_problems"]:
        flags.append("offline scan")
    row["flags"] = flags
    return row


def _source_url(case: Path) -> str:
    try:
        return json.loads(case.read_text(encoding="utf-8-sig")).get("source_url", "")
    except (OSError, ValueError):
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--cases", nargs="*", help="case names (without .json); default: all in cases/")
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--jobs", type=int, default=2, help="runs in parallel (the grader runs one at a time)")
    a = ap.parse_args()
    if not os.environ.get("OPENROUTER_API_KEY"):
        sys.exit("OPENROUTER_API_KEY is not set")
    files = sorted((ROOT / "cases").glob("*.json"))
    if a.cases:
        files = [f for f in files if f.stem in a.cases]
    base = ROOT / "runs" / "harness"
    jobs = [(f, k) for f in files for k in range(1, a.runs + 1)]
    print(f"{len(jobs)} runs ({len(files)} cases x {a.runs}), {a.jobs} at a time ...", flush=True)
    with ThreadPoolExecutor(max_workers=a.jobs) as ex:
        rows = list(ex.map(lambda j: run_one(j[0], j[1], a.model, base), jobs))

    print("\n| case | run | exit | calls | tokens (prompt+completion) | time s | revisions | page | failed checks | flags |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['case']} | {r['run']} | {r['exit']} | {r['calls']} | {r['tokens']} ({r['prompt']}+{r['completion']}) "
              f"| {r['wall_s']} | {r['revisions']} | {r['version']} {r['kb']}KB | {', '.join(r['failed'] or []) or '-'} "
              f"| {'; '.join(r['flags']) or 'ok'} |")
    ok = [r for r in rows if r["tokens"]]
    if ok:
        print(f"\nmedian tokens {statistics.median(r['tokens'] for r in ok):.0f} | "
              f"median time {statistics.median(r['wall_s'] for r in ok):.1f}s | "
              f"exit 0: {sum(r['exit'] == 0 for r in rows)}/{len(rows)} | "
              f"all checks pass: {sum(not r['failed'] for r in rows)}/{len(rows)} | flagged: {sum(bool(r['flags']) for r in rows)}")
    prov = {}
    for r in rows:
        for purpose, p, pt, ct, el in r["per_call"]:
            prov.setdefault((purpose.split("_")[0], p), []).append((pt or 0, ct or 0, el or 0))
    print("\nby call type and provider (mean prompt / completion tokens, seconds):")
    for (purpose, p), v in sorted(prov.items(), key=lambda x: (x[0][0], str(x[0][1]))):
        print(f"  {purpose:<7} {str(p):<14} n={len(v):<2} prompt={statistics.mean(x[0] for x in v):.0f} "
              f"completion={statistics.mean(x[1] for x in v):.0f} time={statistics.mean(x[2] for x in v):.1f}s")
    base.mkdir(parents=True, exist_ok=True)
    (base / "summary.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    print(f"\nsaved {base / 'summary.json'}")


if __name__ == "__main__":
    main()
