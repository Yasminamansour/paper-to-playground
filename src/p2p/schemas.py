"""JSON Schemas sent as response_format (strict). Strict mode rules: every property is
required, additionalProperties is false, and free-form values travel as JSON strings
(*_json fields) that Python parses afterwards."""
from __future__ import annotations


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


S = {"type": "string"}
N = {"type": "number"}
NN = {"type": ["number", "null"]}
STRS = {"type": "array", "items": S}
KV = _obj({"id": S, "value_json": S})  # one control value, e.g. {"id": "p", "value_json": "[0.5,0.5]"}

PLAN_SCHEMA = _obj({
    "concept": S,
    "why_it_matters": S,
    "audience_notes": S,
    "source": _obj({"paper_title": S, "section_label": S, "equation_label": S, "equation_text": S}),
    "grounding_quotes": STRS,
    "symbols": {"type": "array", "items": _obj({"symbol": S, "meaning": S, "units": S})},
    "state": {"type": "array", "items": _obj({
        "id": S,
        "kind": {"type": "string", "enum": ["number", "vector", "matrix", "bool", "choice"]},
        "label": S,
        "default_json": S,
        "min": NN, "max": NN, "step": NN,
        "options": STRS,
    })},
    "outputs": {"type": "array", "items": _obj({"key": S, "meaning": S})},
    "must_show_intermediates": STRS,
    "tests": {"type": "array", "items": _obj({
        "name": S,
        "overrides": {"type": "array", "items": KV},
        "anchors": {"type": "array", "items": _obj({"key": S, "value_js": S})},
        "tol": N,
        "rationale": S,
    })},
    "reference_js": S,
    "invariants": {"type": "array", "items": _obj({"name": S, "js": S})},
    "explorations": {"type": "array", "items": _obj({
        "title": S, "change": S, "observe": S, "why": S,
        "preset": {"type": "array", "items": KV},
    })},
    "limitation": _obj({"kind": {"type": "string", "enum": ["limitation", "assumption", "misconception"]}, "text": S}),
    "simplifications": STRS,
    "visual_idea": S,
})
