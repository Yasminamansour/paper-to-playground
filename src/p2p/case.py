"""Load and validate case.json. Requires source_url, focus, audience; keeps every other field."""
from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

REQUIRED = ("source_url", "focus", "audience")
MAX_FIELD_CHARS = 12_000
# Fields shown first in the context block, in this order. Everything else follows in file order.
PRIORITY = ("excerpt", "title", "paper_title", "section", "equation")


class CaseError(Exception):
    """Bad input file. agent.py maps this to exit code 2."""


@dataclass
class Case:
    source_url: str
    focus: str
    audience: str
    extra: "OrderedDict[str, str]" = field(default_factory=OrderedDict)
    truncated: list = field(default_factory=list)  # names of fields cut to MAX_FIELD_CHARS

    @property
    def excerpt(self) -> str:
        return self.extra.get("excerpt", "")

    def context_block(self) -> str:
        """Compact labelled text for prompts, excerpt first. Fields are already truncated."""
        keys = [k for k in PRIORITY if k in self.extra]
        keys += [k for k in self.extra if k not in keys]
        parts = [f"SOURCE_URL: {self.source_url}", f"AUDIENCE: {self.audience}", f"FOCUS:\n{self.focus}"]
        for k in keys:
            v = self.extra[k]
            label = k.upper()
            parts.append(f"{label}:\n{v}" if "\n" in v or len(v) > 80 else f"{label}: {v}")
        return "\n\n".join(parts)


def _cut(name: str, text: str, truncated: list) -> str:
    if len(text) <= MAX_FIELD_CHARS:
        return text
    truncated.append(name)
    return text[:MAX_FIELD_CHARS] + f"\n[...truncated {len(text) - MAX_FIELD_CHARS} chars]"


def load_case(path: str | Path) -> Case:
    p = Path(path)
    try:
        raw = p.read_text(encoding="utf-8-sig")  # tolerate a BOM (common on Windows)
    except FileNotFoundError:
        raise CaseError(f"input file not found: {p}")
    except (OSError, UnicodeDecodeError) as e:
        raise CaseError(f"cannot read input file {p}: {e}")
    try:
        data = json.loads(raw, object_pairs_hook=OrderedDict)
    except json.JSONDecodeError as e:
        raise CaseError(f"input is not valid JSON: {e}")
    if not isinstance(data, dict):
        raise CaseError("input JSON must be an object")

    missing = [k for k in REQUIRED if k not in data]
    if missing:
        raise CaseError(f"missing required field(s): {', '.join(missing)}")
    bad = [k for k in REQUIRED if not isinstance(data[k], str) or not data[k].strip()]
    if bad:
        raise CaseError(f"field(s) must be non-empty strings: {', '.join(bad)}")

    truncated: list = []
    extra: OrderedDict = OrderedDict()
    for k, v in data.items():
        if k in REQUIRED or v is None:
            continue
        text = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        if text.strip():
            extra[str(k)] = _cut(str(k), text, truncated)

    return Case(
        source_url=data["source_url"].strip(),
        focus=_cut("focus", data["focus"].strip(), truncated),
        audience=_cut("audience", data["audience"].strip(), truncated),
        extra=extra,
        truncated=truncated,
    )
