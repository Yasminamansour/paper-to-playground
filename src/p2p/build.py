"""BUILD call: the model writes page text, control layout hints, visuals and compute_js.
Python fills everything the plan already fixed (symbols, controls, explorations, limitation,
grounding) and adds live self-checks generated from the plan's invariants."""
from __future__ import annotations

import json
import re

from . import llm as llm_mod
from .jsengine import compiles, defines
from .prompts import BUILD_SYSTEM, build_user

BUILD_MAX_TOKENS = 4000
BUILD_RETRY_MAX_TOKENS = 5500
SHORTER = ("Your previous answer was cut off at the token limit. Answer again, shorter: "
           "1-2 sentence texts, at most 2 visuals, compact compute_js.")

FORBIDDEN_JS = re.compile(
    r"\b(fetch|XMLHttpRequest|WebSocket|import|require|eval|Function|document|window|globalThis|"
    r"localStorage|sessionStorage|indexedDB|setTimeout|setInterval|navigator|location|postMessage)\b"
    r"|Math\.random")

WIDGETS = {"slider", "number", "vector", "prob", "matrix", "toggle", "select"}
LAYOUT_KEYS = ("min_len", "max_len", "min_rows", "max_rows", "min_cols", "max_cols", "labels",
               "row_labels", "col_labels", "share_rows", "share_cols", "share_len", "decimals")
VISUAL_KINDS = {"bar", "line", "heatmap", "matrix", "svg"}


class BuildError(llm_mod.LLMError):
    pass


def plan_brief(plan: dict) -> dict:
    """The parts of the plan the BUILD call needs (tests/explorations are filled by Python)."""
    return {
        "concept": plan.get("concept"),
        "why_it_matters": plan.get("why_it_matters"),
        "audience_notes": plan.get("audience_notes"),
        "source": plan.get("source"),
        "symbols": plan.get("symbols"),
        "state": [{k: s.get(k) for k in ("id", "kind", "label", "default", "min", "max", "step", "options")}
                  for s in plan.get("state", [])],
        "outputs": plan.get("outputs"),
        "must_show_intermediates": plan.get("must_show_intermediates"),
        "visual_idea": plan.get("visual_idea"),
        "REFERENCE": plan.get("reference_js"),
    }


def pretty_symbol(s: str) -> str:
    """'d_k' -> d<sub>k</sub>, 'K^T' -> K<sup>T</sup>, 'x_{ij}' -> x<sub>ij</sub> (HTML-lite)."""
    s = str(s or "")
    if "<" in s:
        return s
    s = re.sub(r"\^\{([^}]*)\}|\^([A-Za-z0-9+\-]+)", lambda m: f"<sup>{m.group(1) or m.group(2)}</sup>", s)
    s = re.sub(r"_\{([^}]*)\}|_([A-Za-z0-9]+)", lambda m: f"<sub>{m.group(1) or m.group(2)}</sub>", s)
    return s.replace("sqrt", "√")


def make_controls(plan: dict, hints: list) -> list:
    by_id = {h.get("id"): h for h in hints or [] if isinstance(h, dict)}
    controls = []
    for s in plan.get("state", []):
        sid, kind, d = s["id"], s.get("kind"), s.get("default")
        h = by_id.get(sid, {})
        w = h.get("widget")
        allowed = {"number": {"slider", "number"}, "vector": {"vector", "prob"}, "matrix": {"matrix"},
                   "bool": {"toggle"}, "choice": {"select"}}.get(kind, set())
        if w not in allowed:
            w = {"number": "slider" if s.get("min") is not None and s.get("max") is not None else "number",
                 "vector": "vector", "matrix": "matrix", "bool": "toggle", "choice": "select"}.get(kind, "number")
        c = {"id": sid, "kind": w, "label": h.get("label") or s.get("label") or sid, "default": d}
        if h.get("help"):
            c["help"] = h["help"]
        for k in ("min", "max", "step"):
            if s.get(k) is not None:
                c[k] = s[k]
        if w == "slider" and "step" not in c:
            c["step"] = "any"
        if w == "select":
            c["options"] = [{"value": o, "label": o} for o in s.get("options") or []]
        for k in LAYOUT_KEYS:
            if k in h and h[k] is not None:
                c[k] = h[k]
        controls.append(c)
    return controls


def checks_wrapper(compute_js: str, invariants: list) -> str:
    """Page JS: the model's compute plus live self-checks from the plan's invariants."""
    inv_fns = []
    for inv in invariants or []:
        expr = inv.get("js", "")
        if not expr or compiles(f"function(out, state) {{ return ({expr}); }}"):
            continue
        inv_fns.append("{label: %s, fn: function (out, state) { return (%s); }}" % (json.dumps(inv.get("name", "check")), expr))
    return """(function () {
%s
var __user = compute;
var __inv = [%s];
var __g = typeof window !== 'undefined' ? window : this;
__g.compute = function (state) {
  var r = __user(state) || {};
  var out = r.outputs || {};
  var checks = Array.isArray(r.checks) ? r.checks : [];
  __inv.forEach(function (c) {
    var ok = false, detail = '';
    try { ok = !!c.fn(out, state); } catch (e) { detail = 'could not evaluate: ' + (e && e.message ? e.message : e); }
    checks.push({label: c.label, pass: ok, detail: detail});
  });
  r.checks = checks;
  return r;
};
}).call(this);
""" % (compute_js, ",\n  ".join(inv_fns))


def make_spec(case, plan: dict, b: dict) -> dict:
    src = plan.get("source") or {}
    return {
        "title": b.get("title") or plan.get("concept") or "Interactive explanation",
        "subtitle": b.get("subtitle", ""),
        "audience": case.audience,
        "decimals": 3,
        "idea": {"what": (b.get("idea") or {}).get("what", ""),
                 "equation": (b.get("idea") or {}).get("equation", "") or src.get("equation_text", ""),
                 "why": (b.get("idea") or {}).get("why", "") or plan.get("why_it_matters", "")},
        "playground_intro": b.get("playground_intro", ""),
        "symbols": [{"symbol": pretty_symbol(s.get("symbol")), "meaning": s.get("meaning", ""),
                     "units": pretty_symbol(s.get("units", ""))} for s in plan.get("symbols", [])],
        "controls": make_controls(plan, b.get("controls")),
        "visuals": [v for v in b.get("visuals") or [] if isinstance(v, dict) and v.get("kind") in VISUAL_KINDS][:3],
        "explorations": [{"title": x.get("title", ""), "change": x.get("change", ""), "observe": x.get("observe", ""),
                          "why": x.get("why", ""), "preset": x.get("preset") or {}} for x in plan.get("explorations", [])][:2],
        "limitation": plan.get("limitation") or {},
        "grounding": {
            "paper_title": src.get("paper_title") or case.extra.get("title", "not stated"),
            "section": src.get("section_label") or case.extra.get("section", ""),
            "equation": pretty_symbol(" ".join(x for x in (src.get("equation_label"), src.get("equation_text")) if x)),
            "source_url": case.source_url,
            "from_excerpt": plan.get("grounding_quotes") or [],
            "our_simplifications": plan.get("simplifications") or [],
        },
    }


def validate_build(b: dict) -> list[str]:
    issues = []
    js = b.get("compute_js")
    if not isinstance(js, str) or "function compute" not in js:
        return ["compute_js must define function compute(state)"]
    m = FORBIDDEN_JS.search(js)
    if m:
        issues.append(f"compute_js uses a forbidden API: {m.group(0)}")
    err = defines(js, "compute")
    if err:
        issues.append(f"compute_js does not parse: {err}")
    vis = [v for v in b.get("visuals") or [] if isinstance(v, dict) and v.get("kind") in VISUAL_KINDS]
    if not vis:
        issues.append("no usable visuals")
    return issues


def build(case, plan: dict, *, model, budget, trace, chat=llm_mod.chat) -> dict:
    messages = [{"role": "system", "content": BUILD_SYSTEM},
                {"role": "user", "content": build_user(case.focus, case.audience, plan_brief(plan))}]
    try:
        b, _ = chat(messages, model=model, max_tokens=BUILD_MAX_TOKENS, purpose="build",
                    schema=llm_mod.ANY_JSON, budget=budget, trace=trace, stage="build")
    except llm_mod.Truncated as e:
        trace.revision(1, ["build"], f"truncated: {e}", stage="build")
        messages = messages + [{"role": "user", "content": SHORTER}]
        b, _ = chat(messages, model=model, max_tokens=BUILD_RETRY_MAX_TOKENS, purpose="build_retry",
                    schema=llm_mod.ANY_JSON, budget=budget, trace=trace, stage="build")
    if not isinstance(b, dict):
        raise BuildError("build output is not a JSON object")
    issues = validate_build(b)
    trace.check("build_compute_valid", not issues, issues or "ok", stage="build")
    if any("compute_js" in i for i in issues):
        raise BuildError("; ".join(issues))
    spec = make_spec(case, plan, b)
    result = {"raw": b, "spec": spec, "compute_js": b["compute_js"],
              "page_js": checks_wrapper(b["compute_js"], plan.get("invariants"))}
    trace.event("build", "build_summary", "info", n_controls=len(spec["controls"]),
                n_visuals=len(spec["visuals"]), visual_kinds=[v["kind"] for v in spec["visuals"]],
                compute_chars=len(b["compute_js"]))
    return result
