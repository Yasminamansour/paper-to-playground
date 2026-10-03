"""Dev-only browser smoke test (never imported by agent.py).

Opens a generated page in headless Chromium with ALL network blocked, operates every control,
and fails on console errors, page errors, network attempts, a visible error box or empty visuals.

  pip install -r requirements-dev.txt && python -m playwright install chromium   (once)
  python tools/smoke_browser.py out/index.html [--screenshot shot.png]
"""
import argparse
import os
import sys
from pathlib import Path


def smoke(path: Path, screenshot=None, expect_error=False) -> list[str]:
    from playwright.sync_api import sync_playwright

    problems: list[str] = []
    with sync_playwright() as p:
        exe = os.environ.get("P2P_CHROMIUM")  # optional: path to an existing Chromium
        browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 900})
        page.on("console", lambda m: m.type == "error" and problems.append(f"console: {m.text}"))
        page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))

        def block(route):
            if not route.request.url.startswith(("file:", "data:", "about:")):
                problems.append(f"network: {route.request.url}")
            return route.abort() if not route.request.url.startswith("file:") else route.continue_()

        page.route("**/*", block)
        page.goto(path.resolve().as_uri())
        page.wait_for_timeout(300)

        err_visible = page.is_visible("#error-box")
        if expect_error:
            if not err_visible:
                problems.append("error box did not appear for broken compute")
            browser.close()
            return problems
        if err_visible:
            problems.append("error box visible: " + page.inner_text("#error-box"))
        for sid in ("idea", "symbols", "playground", "explore-1", "explore-2", "limitation", "grounding"):
            if not page.query_selector(f"#{sid}"):
                problems.append(f"missing section #{sid}")

        def snapshot():
            return page.inner_text("#playground")

        before = snapshot()
        changed = 0
        for r in page.query_selector_all("#playground input[type=range]"):
            r.focus()
            page.keyboard.press("ArrowRight")  # keyboard operation
            page.keyboard.press("ArrowRight")
            changed += snapshot() != before
            before = snapshot()
        for n in page.query_selector_all("#playground input[type=number]")[:12]:
            n.fill(str(float(n.input_value() or 0) + 1))
            changed += snapshot() != before
            before = snapshot()
        for c in page.query_selector_all("#playground input[type=checkbox]"):
            c.click()
        for s in page.query_selector_all("#playground select"):
            opts = s.eval_on_selector_all("option", "os => os.map(o => o.value)")
            if len(opts) > 1:
                s.select_option(opts[-1])
                changed += snapshot() != before
                before = snapshot()
        # buttons rebuild the controls, so look them up again before each click
        for sel in ("#playground button", "section[id^=explore] button"):
            for i in range(len(page.query_selector_all(sel))):
                btns = page.query_selector_all(sel)
                if i < len(btns) and btns[i].is_enabled():
                    btns[i].click()
        if changed == 0:
            problems.append("no control changed the playground")
        if page.is_visible("#error-box"):
            problems.append("error box visible after interaction: " + page.inner_text("#error-box"))
        figs = page.query_selector_all("#playground figure")
        if not figs:
            problems.append("no visuals rendered")
        for f in figs:
            if not f.query_selector("svg, table"):
                problems.append("empty visual: " + (f.inner_text()[:80]))
        if screenshot:
            page.click("text=Reset to defaults")
            page.screenshot(path=screenshot, full_page=True)
        browser.close()
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("page")
    ap.add_argument("--screenshot")
    ap.add_argument("--expect-error", action="store_true")
    a = ap.parse_args()
    problems = smoke(Path(a.page), a.screenshot, a.expect_error)
    for pr in problems:
        print("PROBLEM:", pr)
    print("smoke OK" if not problems else f"smoke FAILED ({len(problems)})")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
