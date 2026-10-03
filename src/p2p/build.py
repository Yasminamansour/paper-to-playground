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
COMPUTE_FIX_MAX_TOKENS = 2500
SHORTER = ("Your previous answer was cut off at the token limit. Answer again, shorter: "
           "1-2 sentence texts, at most 2 visuals, compact compute_js.")

FORBIDDEN_JS = re.compile(
    r"\b(?:window|document|globalThis|self|navigator|location|parent|top)\s*[.\[]"   # real browser access, not a variable named "window"
    r"|\b(?:fetch|eval|require|importScripts|setTimeout|setInterval)\s*\(|\bimport\s*\(|\bnew\s+Function\b|\bFunction\s*\("
    r"|\b(?:XMLHttpRequest|WebSocket|EventSource|localStorage|sessionStorage|indexedDB)\b|Math\.random")

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


URL_IN_TEXT = re.compile(r"(?:https?:)?//[A-Za-z0-9.-]+\.[A-Za-z]{2,}[^\s\"'<>)]*", re.I)


def _strip_urls(obj, key=""):
    """Remove URLs from generated text; the page may only show source_url (as plain text)."""
    if isinstance(obj, dict):
        return {k: (v if k == "source_url" else _strip_urls(v, k)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_strip_urls(v, key) for v in obj]
    if isinstance(obj, str):
        return URL_IN_TEXT.sub("", obj)
    return obj


def make_spec(case, plan: dict, b: dict) -> dict:
    return _strip_urls(_make_spec(case, plan, b))


def _make_spec(case, plan: dict, b: dict) -> dict:
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


def assemble_build(case, plan: dict, b: dict) -> dict:
    """Turn a raw build answer into everything the page and the checks need."""
    return {"raw": b, "spec": make_spec(case, plan, b), "compute_js": b["compute_js"],
            "page_js": checks_wrapper(b["compute_js"], plan.get("invariants"))}


def fallback_build(plan: dict) -> dict:
    """No model call: a plain but working page that runs the plan's reference function.
    Used when the BUILD call fails, so the run still produces a usable page."""
    call = plan.get("reference_call") or {"name": "reference", "params": ["state"]}
    args = "state" if call["params"] == ["state"] else ", ".join(f"state[{json.dumps(p)}]" for p in call["params"])
    shows = plan.get("must_show_intermediates") or [o["key"] for o in plan.get("outputs", [])]
    meaning = {o["key"]: o.get("meaning", "") for o in plan.get("outputs", [])}
    compute_js = (plan.get("reference_js", "") + "\nfunction compute(state) {\n"
                  f"  var out = {call['name']}({args});\n"
                  f"  var show = {json.dumps(shows)};\n  var notes = {json.dumps(meaning)};\n"
                  "  return { outputs: out, intermediates: show.filter(function (k) { return k in out; })"
                  ".map(function (k) { return { label: k, value: out[k], note: notes[k] || '' }; }) };\n}")
    visuals = []
    sample = {}
    try:
        from .jsengine import call_fn
        sample = call_fn(compute_js, "compute", {s["id"]: s.get("default") for s in plan.get("state", [])}).get("outputs", {})
    except Exception:  # noqa: BLE001 - a fallback must never crash
        pass
    for o in plan.get("outputs", []):
        v = sample.get(o["key"])
        if isinstance(v, list) and v and isinstance(v[0], list):
            visuals.append({"kind": "heatmap", "title": o["key"], "data": o["key"], "caption": o.get("meaning", "")})
        elif isinstance(v, list) and v and all(isinstance(x, (int, float)) for x in v):
            visuals.append({"kind": "bar", "title": o["key"], "data": o["key"], "caption": o.get("meaning", "")})
    if not visuals:
        keys = [o["key"] for o in plan.get("outputs", []) if isinstance(sample.get(o["key"]), (int, float))]
        visuals.append({"kind": "bar", "title": "Results", "data": "__values", "labels": "__names", "caption": ""})
        compute_js = compute_js.replace("return { outputs: out,", "out.__values = %s.map(function (k) { return out[k]; });"
                                        " out.__names = %s;\n  return { outputs: out," % (json.dumps(keys), json.dumps(keys)))
    return {"title": plan.get("concept") or "Interactive explanation", "subtitle": "",
            "idea": {"what": plan.get("concept", ""), "equation": "", "why": plan.get("why_it_matters", "")},
            "playground_intro": "", "controls": [], "visuals": visuals[:3], "compute_js": compute_js}


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
    if any("compute_js" in i for i in issues) and budget.can_call(COMPUTE_FIX_MAX_TOKENS):
        # one small call to fix only compute_js, instead of losing the whole page
        trace.check("build_compute_valid", False, issues, stage="build")
        trace.revision(1, ["compute_js"], "; ".join(issues)[:200], stage="build")
        fix = messages + [{"role": "assistant", "content": json.dumps({"compute_js": b.get("compute_js", "")})},
                          {"role": "user", "content": "compute_js has problems: " + "; ".join(issues)
                           + '. Return {"compute_js": "<corrected full source>"} only.'}]
        try:
            patch, _ = chat(fix, model=model, max_tokens=COMPUTE_FIX_MAX_TOKENS, purpose="build_fix",
                            schema=llm_mod.ANY_JSON, budget=budget, trace=trace, stage="build")
            if isinstance(patch, dict) and isinstance(patch.get("compute_js"), str):
                b["compute_js"] = patch["compute_js"]
                issues = validate_build(b)
        except llm_mod.LLMError as e:
            trace.event("build", "build_fix", "fail", error=f"{type(e).__name__}: {e}")
    trace.check("build_compute_valid", not issues, issues or "ok", stage="build")
    if any("compute_js" in i for i in issues):
        raise BuildError("; ".join(issues))
    result = assemble_build(case, plan, b)
    spec = result["spec"]
    trace.event("build", "build_summary", "info", n_controls=len(spec["controls"]),
                n_visuals=len(spec["visuals"]), visual_kinds=[v["kind"] for v in spec["visuals"]],
                compute_chars=len(b["compute_js"]))
    return result
