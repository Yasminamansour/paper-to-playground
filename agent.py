"""Paper to Playground agent.

python agent.py --input case.json --output out --model MODEL_ID
Writes out/index.html (one offline file) and out/trace.jsonl.
Exit codes: 0 page written with no critical check failures, 1 generation failed, 2 bad input or usage.
"""
import time

T0 = time.monotonic()  # process start: trace times and the deadline are measured from here

import argparse  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from p2p.assemble import assemble  # noqa: E402
from p2p.budget import Budget  # noqa: E402
from p2p.build import assemble_build, build, fallback_build  # noqa: E402
from p2p.case import CaseError, load_case  # noqa: E402
from p2p.llm import LLMError, MissingKey  # noqa: E402
from p2p.plan import make_plan  # noqa: E402
from p2p.repair import repair_loop  # noqa: E402
from p2p.trace import Trace  # noqa: E402

EXIT_OK, EXIT_FAIL, EXIT_INPUT = 0, 1, 2
WATCHDOG_S = 570  # hard stop well before the 10-minute limit

PLACEHOLDER = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Dry run</title></head>
<body><h1>Dry run</h1><p>Placeholder page. No model calls were made.</p></body></html>
"""


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Turn a paper excerpt into an interactive playground page.")
    p.add_argument("--input", required=True, help="path to case.json")
    p.add_argument("--output", required=True, help="output directory")
    p.add_argument("--model", required=True, help="OpenRouter MODEL_ID")
    p.add_argument("--dry-run", action="store_true", help="load the case, write a placeholder page, no API calls")
    p.add_argument("--stop-after", choices=["plan"], help="dev only: stop after this stage and save its JSON")
    p.add_argument("--no-repair", action="store_true", help="dev only: run checks but never call repair")
    p.add_argument("--inject-fault", choices=["compute", "visuals"], help="dev only: break the build to test repair")
    return p.parse_args(argv)


class Keeper:
    """Holds the best page so far. The watchdog writes it if time runs out."""

    def __init__(self, out: Path, trace: Trace, budget: Budget):
        self.out, self.trace, self.budget = out, trace, budget
        self.best = None
        self.done = False
        self.lock = threading.Lock()

    def offer(self, version):
        with self.lock:
            if self.best is None or version.score < self.best.score:
                self.best = version

    def write(self, version) -> int:
        (self.out / "index.html").write_text(version.html, encoding="utf-8", newline="\n")
        crit, major, minor = version.score
        self.trace.event("assemble", "write_page", "ok", version=version.label, bytes=len(version.html.encode("utf-8")),
                         critical=crit, major=major, minor=minor,
                         failed=[r.name for r in version.failures])
        return EXIT_OK if crit == 0 else EXIT_FAIL

    def timeout(self):
        with self.lock:
            if self.done:
                return
            self.done = True
            code = EXIT_FAIL
            self.trace.event("watchdog", "timeout", "fail", seconds=WATCHDOG_S)
            if self.best is not None:
                code = self.write(self.best)
            self.trace.summary(self.budget.totals(), ok=code == EXIT_OK, exit_code=code)
            self.trace.close()
        os._exit(code)


def inject_fault(built: dict, kind: str) -> dict:
    raw = json.loads(json.dumps(built["raw"]))
    if kind == "compute":  # every number in the outputs comes out 50% too big
        raw["compute_js"] += ("\nvar __orig = compute;\ncompute = function (s) { var r = __orig(s); "
                              "var f = function (v) { return Array.isArray(v) ? v.map(f) : (typeof v === 'number' ? v * 1.5 : v); }; "
                              "Object.keys(r.outputs).forEach(function (k) { r.outputs[k] = f(r.outputs[k]); }); return r; };")
    else:
        for v in raw.get("visuals", []):
            v["data"] = "missing_output"
            v.pop("series", None)
    return raw


def run(args, trace: Trace, budget: Budget, keeper: Keeper) -> int:
    try:
        case = load_case(args.input)
    except CaseError as e:
        trace.event("load", "load_case", "fail", error=str(e))
        print(f"error: {e}", file=sys.stderr)
        return EXIT_INPUT
    trace.event("load", "load_case", "ok", fields=["source_url", "focus", "audience", *case.extra.keys()],
                has_excerpt=bool(case.excerpt), context_chars=len(case.context_block()))
    for name in case.truncated:
        trace.event("load", "truncate_field", "info", field=name)

    out = Path(args.output)
    if args.dry_run:
        (out / "index.html").write_text(PLACEHOLDER, encoding="utf-8")
        trace.event("assemble", "write_placeholder", "ok", path="index.html")
        return EXIT_OK

    # 1. PLAN: concept, controls and the answer key
    try:
        plan, issues = make_plan(case, model=args.model, budget=budget, trace=trace)
    except MissingKey as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_FAIL
    except LLMError as e:
        trace.event("plan", "make_plan", "fail", error=f"{type(e).__name__}: {e}")
        print(f"error: plan failed: {e}", file=sys.stderr)
        return EXIT_FAIL
    (out / "plan.json").write_text(json.dumps(plan, indent=1, ensure_ascii=False), encoding="utf-8")
    if args.stop_after == "plan":
        trace.event("plan", "stop_after", "info", issues=len(issues))
        return EXIT_OK

    # 2. BUILD: page text, visuals, compute(); fall back to a plain page if the call fails
    try:
        built = build(case, plan, model=args.model, budget=budget, trace=trace)
    except LLMError as e:
        trace.event("build", "build", "fail", error=f"{type(e).__name__}: {e}")
        if not plan.get("answer_key", {}).get("reference_ok"):
            print(f"error: build failed: {e}", file=sys.stderr)
            return EXIT_FAIL
        trace.event("build", "fallback_page", "info", reason="build failed; page uses the plan's reference function")
        built = assemble_build(case, plan, fallback_build(plan))
    if args.inject_fault:
        built = assemble_build(case, plan, inject_fault(built, args.inject_fault))
        trace.event("build", "inject_fault", "info", kind=args.inject_fault)

    # 3. CHECK and REPAIR only what failed; keep the best version
    best = repair_loop(case, plan, built, model=args.model, budget=budget, trace=trace, assemble=assemble,
                       max_rounds=0 if args.no_repair else 2, on_version=keeper.offer)
    (out / "build.json").write_text(json.dumps(best.built["raw"], indent=1, ensure_ascii=False), encoding="utf-8")
    with keeper.lock:
        if keeper.done:
            return EXIT_FAIL
        return keeper.write(best)


def main(argv=None) -> int:
    args = parse_args(argv)  # argparse exits with code 2 on bad usage
    out = Path(args.output)
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"error: cannot create output dir {out}: {e}", file=sys.stderr)
        return EXIT_INPUT
    trace = Trace(out, t0=T0)
    budget = Budget(t0=T0)
    keeper = Keeper(out, trace, budget)
    timer = threading.Timer(max(1.0, WATCHDOG_S - (time.monotonic() - T0)), keeper.timeout)
    timer.daemon = True
    timer.start()
    trace.event("start", "args", "info", model=args.model, dry_run=args.dry_run)
    code = EXIT_FAIL
    try:
        code = run(args, trace, budget, keeper)
    except Exception as e:  # last resort: never die without a page (if any) and a trace summary
        trace.event("error", "unhandled", "fail", error=f"{type(e).__name__}: {e}")
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        with keeper.lock:
            code = keeper.write(keeper.best) if keeper.best is not None and not keeper.done else EXIT_FAIL
    finally:
        timer.cancel()
        with keeper.lock:
            if not keeper.done:
                keeper.done = True
                trace.summary(budget.totals(), ok=(code == EXIT_OK), exit_code=code)
                trace.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
