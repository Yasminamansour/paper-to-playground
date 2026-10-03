"""Paper to Playground agent.

python agent.py --input case.json --output out --model MODEL_ID
Exit codes: 0 success, 1 generation failed, 2 bad input or usage.
"""
import time

T0 = time.monotonic()  # process start: trace times and the deadline are measured from here

import argparse  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from p2p.budget import Budget  # noqa: E402
from p2p.case import CaseError, load_case  # noqa: E402
from p2p.llm import LLMError, MissingKey  # noqa: E402
from p2p.assemble import write_page  # noqa: E402
from p2p.build import build  # noqa: E402
from p2p.plan import make_plan  # noqa: E402
from p2p.trace import Trace  # noqa: E402

EXIT_OK, EXIT_FAIL, EXIT_INPUT = 0, 1, 2

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
    p.add_argument("--stop-after", choices=["plan", "build"], help="dev only: stop after this stage and save its JSON")
    return p.parse_args(argv)


def run(args, trace: Trace, budget: Budget) -> int:
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

    try:
        built = build(case, plan, model=args.model, budget=budget, trace=trace)
    except LLMError as e:
        trace.event("build", "build", "fail", error=f"{type(e).__name__}: {e}")
        print(f"error: build failed: {e}", file=sys.stderr)
        return EXIT_FAIL
    (out / "build.json").write_text(json.dumps(built["raw"], indent=1, ensure_ascii=False), encoding="utf-8")
    path = write_page(out, built["spec"], built["page_js"])
    trace.event("assemble", "write_page", "ok", path="index.html", bytes=path.stat().st_size)
    return EXIT_OK


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
    trace.event("start", "args", "info", model=args.model, dry_run=args.dry_run)
    code = EXIT_FAIL
    try:
        code = run(args, trace, budget)
    except Exception as e:  # last resort: never die without a trace summary
        trace.event("error", "unhandled", "fail", error=f"{type(e).__name__}: {e}")
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        code = EXIT_FAIL
    finally:
        trace.summary(budget.totals(), ok=(code == EXIT_OK), exit_code=code)
        trace.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
