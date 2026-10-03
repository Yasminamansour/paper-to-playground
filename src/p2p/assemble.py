"""Build the single self-contained out/index.html from the template, a SPEC dict and compute_js.

All SPEC strings pass through an allowlist sanitizer: a few text tags plus MathML Core,
no attributes except two harmless MathML ones, no URLs, no scripts.
"""
from __future__ import annotations

import html
import json
import re
from html.parser import HTMLParser
from pathlib import Path

TEMPLATE_DIR = Path(__file__).resolve().parent / "template"

TEXT_TAGS = {"b", "i", "em", "strong", "sub", "sup", "code", "br", "span"}
MATH_TAGS = {"math", "mi", "mn", "mo", "mrow", "msub", "msup", "msubsup", "mfrac", "msqrt", "mroot",
             "mover", "munder", "munderover", "mtext", "mtable", "mtr", "mtd", "mspace", "mstyle"}
ALLOWED = TEXT_TAGS | MATH_TAGS
VOID = {"br", "mspace"}
ALLOWED_ATTRS = {"math": {"display"}, "mi": {"mathvariant"}, "mstyle": {"displaystyle"}}
ATTR_VALUE = re.compile(r"^[A-Za-z-]{1,20}$")
DROP_CONTENT = {"script", "style", "iframe", "object", "embed", "template", "noscript", "svg", "textarea"}


class _Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in DROP_CONTENT:
            self.skip += 1
            return
        if self.skip or tag not in ALLOWED:
            return
        keep = ALLOWED_ATTRS.get(tag, set())
        a = "".join(f' {k}="{v}"' for k, v in attrs if k in keep and v and ATTR_VALUE.match(v))
        if tag in VOID:
            self.out.append(f"<{tag}{a}>")
        else:
            self.out.append(f"<{tag}{a}>")
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if not self.skip and tag in VOID:
            self.out.append(f"<{tag}>")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in DROP_CONTENT:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip or tag not in self.stack:
            return
        while self.stack:  # close anything left open inside it
            t = self.stack.pop()
            self.out.append(f"</{t}>")
            if t == tag:
                break

    def handle_data(self, data):
        if not self.skip:
            self.out.append(html.escape(data, quote=False))

    def result(self) -> str:
        while self.stack:
            self.out.append(f"</{self.stack.pop()}>")
        return "".join(self.out)


def sanitize_html(s: str) -> str:
    p = _Sanitizer()
    p.feed(str(s))
    p.close()
    return p.result()


def sanitize_spec(obj):
    """Sanitize every string in the SPEC (keys are kept as they are)."""
    if isinstance(obj, dict):
        return {str(k): sanitize_spec(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_spec(v) for v in obj]
    if isinstance(obj, str):
        return sanitize_html(obj)
    if isinstance(obj, float) and (obj != obj or obj in (float("inf"), float("-inf"))):
        return None  # JSON has no NaN/Infinity
    return obj


def embed_json(obj) -> str:
    """JSON safe to place inside <script type="application/json">."""
    s = json.dumps(obj, ensure_ascii=False, allow_nan=False)
    return s.replace("</", "<\\/").replace("<!--", "<\\!--").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


_SCRIPT_CLOSE = re.compile(r"</(script)", re.I)


def embed_js(code: str) -> str:
    """JS safe to place inside a <script> element."""
    return _SCRIPT_CLOSE.sub(r"<\\/\1", code).replace("<!--", "<\\!--")


def _read(name: str) -> str:
    return (TEMPLATE_DIR / name).read_text(encoding="utf-8")


def assemble(spec: dict, compute_js: str) -> str:
    clean = sanitize_spec(spec)
    title = re.sub(r"<[^>]+>", "", clean.get("title") or "Interactive explanation")
    page = _read("page.html")
    parts = {
        "{{TITLE}}": html.escape(html.unescape(title), quote=False),
        "{{STYLE}}": _read("style.css"),
        "{{SPEC_JSON}}": embed_json(clean),
        "{{COMPUTE_JS}}": embed_js(compute_js or ""),
        "{{RUNTIME_JS}}": embed_js(_read("runtime.js")),
    }
    # one pass, so placeholder-looking text inside the content is never expanded
    return re.sub("|".join(re.escape(k) for k in parts), lambda m: parts[m.group(0)], page)


def write_page(out_dir, spec: dict, compute_js: str) -> Path:
    path = Path(out_dir) / "index.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(assemble(spec, compute_js), encoding="utf-8", newline="\n")
    return path
