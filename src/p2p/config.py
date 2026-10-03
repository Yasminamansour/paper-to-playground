"""Per-model request settings, keyed ONLY by the MODEL_ID string (never by paper or case).

reasoning:     OpenRouter `reasoning` object, or None to send nothing.
json_mode:     "schema" = response_format json_schema (strict); "object" = json_object; "none" = prompt only.
require_params: ask OpenRouter to route only to providers that support every parameter we send.
"""
from __future__ import annotations

DEFAULT = {
    "reasoning": {"effort": "low", "exclude": True},
    "json_mode": "schema",
    "require_params": True,
    "provider_sort": "throughput",
    "temperature": 0.0,  # same input -> as close to the same plan as possible (graded twice)
}

# First matching prefix wins. Tune these with tools/ping.py.
BY_PREFIX = [
    ("deepseek/", {"reasoning": {"enabled": False, "exclude": True}}),
]


def settings_for(model: str) -> dict:
    out = dict(DEFAULT)
    for prefix, over in BY_PREFIX:
        if model.startswith(prefix):
            out.update(over)
            break
    return out
