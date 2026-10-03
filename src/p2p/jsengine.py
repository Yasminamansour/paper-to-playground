"""Sandboxed JavaScript evaluation with QuickJS (pip package quickjs-ng, imported as `quickjs`).

Each call gets a fresh context with a time limit and a memory limit. The context has no
network, file or DOM APIs. Values cross back to Python through JSON.stringify, so only
plain data (numbers, strings, booleans, arrays, objects) comes out. NaN/Infinity become null.
"""
from __future__ import annotations

import json

import quickjs

TIME_LIMIT_S = 1.0
MEMORY_LIMIT = 32 * 1024 * 1024


class JSError(Exception):
    pass


def new_context(time_limit: float = TIME_LIMIT_S) -> "quickjs.Context":
    ctx = quickjs.Context()
    ctx.set_time_limit(time_limit)
    ctx.set_memory_limit(MEMORY_LIMIT)
    return ctx


def eval_js(expr: str, time_limit: float = TIME_LIMIT_S):
    """Evaluate one JS expression and return it as Python data."""
    ctx = new_context(time_limit)
    try:
        out = ctx.eval(f"JSON.stringify(({expr}))")
    except quickjs.JSException as e:
        raise JSError(str(e).splitlines()[0][:200]) from None
    if out is None:  # undefined
        raise JSError("expression has no value")
    return json.loads(out)


def compiles(fn_source: str) -> str | None:
    """Return None if the JS source parses, else the error message."""
    ctx = new_context(0.5)
    try:
        ctx.eval(f"(function(){{ return typeof ({fn_source}); }})()")
        return None
    except quickjs.JSException as e:
        return str(e).splitlines()[0][:200]


def call_fn(source: str, fn_name: str, *args, time_limit: float = TIME_LIMIT_S):
    """Define `source` (which must declare function fn_name), call it with JSON args, return data."""
    ctx = new_context(time_limit)
    arg_src = ", ".join(f"JSON.parse({json.dumps(json.dumps(a))})" for a in args)
    try:
        ctx.eval(source)
        out = ctx.eval(f"JSON.stringify({fn_name}({arg_src}))")
    except quickjs.JSException as e:
        raise JSError(str(e).splitlines()[0][:200]) from None
    if out is None:
        raise JSError(f"{fn_name} returned nothing")
    return json.loads(out)


def eval_bool(expr: str, out, state, time_limit: float = 0.5) -> bool:
    """Evaluate a boolean invariant over `out` and `state`."""
    src = f"function __inv(out, state) {{ return !!({expr}); }}"
    return bool(call_fn(src, "__inv", out, state, time_limit=time_limit))


def close(a, b, tol: float) -> bool:
    """Numbers or nested lists equal within tol (absolute, or relative for big values)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= max(tol, tol * abs(b))
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(close(x, y, tol) for x, y in zip(a, b))
    return a == b
