"""Assemble tests/fixtures/generic_spec.json into one HTML file for a manual browser check.

  python tools/render_fixture.py                 # writes <temp dir>/fixture.html
  python tools/render_fixture.py --out x.html --break   # deliberately broken compute, to see the error box
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from p2p.assemble import assemble  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(tempfile.gettempdir()) / "fixture.html"))
    ap.add_argument("--break", dest="broken", action="store_true", help="make compute throw")
    a = ap.parse_args()
    fx = json.loads((ROOT / "tests/fixtures/generic_spec.json").read_text(encoding="utf-8"))
    code = fx["compute_js"]
    if a.broken:
        code = code.replace("function compute(state) {",
                            "function compute(state) {\n  throw new Error('deliberate test error');", 1)
    page = assemble(fx["spec"], code)
    out = Path(a.out)
    out.write_text(page, encoding="utf-8", newline="\n")
    print(f"wrote {out} ({len(page.encode('utf-8')) / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
