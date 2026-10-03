"""Static offline check for any generated page (same rules as the agent's own checks).

  python tools/offline_scan.py out/index.html [--source-url URL]
Exit 0 if clean, 1 if anything could reach the network.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from p2p.checks import NETWORK_RE, URL_RE  # noqa: E402


def scan(html: str, source_url: str = "") -> list[str]:
    text = html.replace(source_url, "") if source_url else html
    problems = [f"url: {u}" for u in sorted(set(URL_RE.findall(text)))]
    problems += [f"network code: {m.group(0)[:40]}" for m in NETWORK_RE.finditer(html)]
    if len(html.encode("utf-8")) > 400_000:
        problems.append("page over 400 KB")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("page")
    ap.add_argument("--source-url", default="")
    a = ap.parse_args()
    probs = scan(Path(a.page).read_text(encoding="utf-8"), a.source_url)
    for p in probs:
        print("PROBLEM", p)
    print("offline scan OK" if not probs else f"{len(probs)} problem(s)")
    return 1 if probs else 0


if __name__ == "__main__":
    sys.exit(main())
