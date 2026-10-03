"""Put copied paper text into a case file as its "excerpt" (handles quotes, line breaks, unicode).

  1. Select the section text in your browser and press Ctrl+C.
  2. python tools/set_excerpt.py cases/a_attention.json
Reads the Windows clipboard. Or pass a text file: --from excerpt.txt
Fix a file where the text was pasted by hand: --repair   (add --start "We call" to drop a stray heading)
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


def read_clipboard() -> str:
    cmd = ["powershell", "-NoProfile", "-Command",
           "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-Clipboard -Raw"]
    try:
        out = subprocess.run(cmd, capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as e:
        sys.exit(f"could not read the clipboard ({e}); use --from FILE instead")
    return out.decode("utf-8", "replace").lstrip("﻿")


INVISIBLE = dict.fromkeys(map(ord, "\u2061\u2062\u2063\u2064\u200b\u200c\u200d\ufeff"), None)


def clean(text: str) -> str:
    """Tidy text copied from a web page. Math on arXiv HTML pages copies as one symbol per
    line; single line breaks become spaces, blank lines stay as paragraph breaks."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    # drop lines that held only invisible math characters, so they don't look like paragraph breaks
    text = "\n".join(l.translate(INVISIBLE) for l in text.split("\n") if not (l.strip() and not l.translate(INVISIBLE).strip()))
    paras = re.split(r"\n\s*\n", text)
    out = []
    for p in paras:
        p = re.sub(r"\s*\n\s*", " ", p)
        p = re.sub(r"[ \t]+", " ", p).strip()
        p = re.sub(r" ([,.;:)\]])", r"\1", p)  # "d k ," -> "d k,"
        p = re.sub(r"([(\[]) ", r"\1", p)
        if p:
            out.append(p)
    return "\n\n".join(out)


def repair(raw: str) -> dict:
    """Recover a case file whose excerpt was pasted by hand (raw line breaks, unescaped quotes)."""
    m = re.search(r'"excerpt"\s*:\s*"', raw)
    if not m:
        raise ValueError("no \"excerpt\" field found")
    end = raw.rstrip().rstrip("}").rstrip()
    if not end.endswith('"'):
        raise ValueError("expected the excerpt to be the last field")
    head = raw[:m.start()].rstrip().rstrip(",") + "\n}"
    data = json.loads(head)
    data["excerpt"] = end[m.end():-1]
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("case")
    ap.add_argument("--from", dest="src", help="read the excerpt from this text file instead of the clipboard")
    ap.add_argument("--repair", action="store_true", help="fix a case file where the excerpt was pasted by hand")
    ap.add_argument("--start", help="drop everything before the first occurrence of this text")
    a = ap.parse_args()
    path = Path(a.case)
    raw = path.read_text(encoding="utf-8-sig")
    if a.repair:
        data = repair(raw)
        text = data["excerpt"]
    else:
        data = json.loads(raw)
        text = Path(a.src).read_text(encoding="utf-8-sig") if a.src else read_clipboard()
    text = clean(text)
    if a.start:
        i = text.find(a.start)
        if i == -1:
            sys.exit(f"start text not found: {a.start!r}")
        text = text[i:]
    if len(text) < 200:
        sys.exit(f"only {len(text)} characters copied; copy the whole section and try again")
    data["excerpt"] = text
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"saved {len(text)} characters into {path}")
    print("starts:", text[:70].replace("\n", " "), "...")
    print("ends:  ...", text[-70:].replace("\n", " "))


if __name__ == "__main__":
    main()
