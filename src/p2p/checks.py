"""Deterministic checks on the built page. No model calls.

Each check is a CheckResult. passed=None means "skipped" (never counted as a pass).
severity: critical (page unusable or unsafe), major (wrong or dead), minor (polish).
target names the part a repair should change: compute_js, visuals, text, plan.reference, ...
"""
from __future__ import annotations

import json
import math
import random
import re
from dataclasses import dataclass, field

from .jsengine import JSError, call_fn, close, eval_bool

try:  # the engine is a pinned dependency, but never claim a pass without it
    import quickjs  # noqa: F401
    ENGINE = True
except ImportError:  # pragma: no cover
    ENGINE = False

MAX_PAGE_BYTES = 400_000
SEED = 1234


@dataclass
class CheckResult:
    name: str
    passed: bool | None
    severity: str = "major"
    detail: object = ""
    target: str = ""
    data: dict = field(default_factory=dict)


def _r(name, passed, severity="major", detail="", target="", **data):
    return CheckResult(name, passed, severity, detail, target, data)


# ---------------------------------------------------------------- static checks

URL_RE = re.compile(r"(?:https?:)?//[A-Za-z0-9.-]+\.[A-Za-z]{2,}[^\s\"'<>)]*", re.I)
NETWORK_RE = re.compile(
    r"\bfetch\s*\(|XMLHttpRequest|WebSocket|EventSource|sendBeacon|\bimport\s*\(|@import|"
    r"<script[^>]+\bsrc\s*=|<link[^>]+\bhref\s*=|<iframe|<object|<embed|<base\b|url\(\s*['\"]?(?!#)", re.I)


def static_checks(case, spec: dict, html: str) -> list[CheckResult]:
    out = []
    # offline: the only URL allowed anywhere is the case's source_url, shown as text
    stripped = html.replace(case.source_url, "") if case.source_url else html
    urls = sorted(set(URL_RE.findall(stripped)))
    net = sorted(set(m.group(0)[:30] for m in NETWORK_RE.finditer(html)))
    out.append(_r("offline_no_urls", not urls, "critical", urls[:5] or "only source_url as text", "page"))
    out.append(_r("offline_no_network_code", not net, "critical", net[:5] or "ok", "page"))
    size = len(html.encode("utf-8"))
    out.append(_r("single_file_size", size < MAX_PAGE_BYTES, "critical", f"{size / 1024:.1f} KB", "page"))
    out.append(_r("disclaimer_present", "does not reproduce" in html, "major", "", "page"))

    idea = spec.get("idea") or {}
    g = spec.get("grounding") or {}
    ex = spec.get("explorations") or []
    sections = {
        "idea": bool(_text(idea.get("what"))),
        "symbols": len(spec.get("symbols") or []) >= 2,
        "playground": _n_inputs(spec.get("controls") or []) >= 2 and len(spec.get("visuals") or []) >= 1,
        "explore-1": len(ex) > 0 and all(_text(ex[0].get(k)) for k in ("change", "observe", "why")),
        "explore-2": len(ex) > 1 and all(_text(ex[1].get(k)) for k in ("change", "observe", "why")),
        "limitation": bool(_text((spec.get("limitation") or {}).get("text"))),
        "grounding": bool(_text(g.get("paper_title"))) and bool(g.get("our_simplifications")),
    }
    missing = [k for k, ok in sections.items() if not ok]
    out.append(_r("required_sections", not missing, "critical", missing or "all 7 present", "text"))
    out.append(_r("grounding_labels", bool(_text(g.get("section"))) and bool(_text(g.get("equation"))),
                  "minor", {"section": g.get("section"), "equation": g.get("equation")}, "text"))
    if case.excerpt:
        quotes = g.get("from_excerpt") or []
        ws = lambda t: re.sub(r"\s+", " ", t).strip()
        ex_n = ws(case.excerpt)                      # raw excerpt text: never strip "tags" from it
        bad = [q for q in quotes if ws(_text(q)) not in ex_n]  # quotes are sanitized HTML-lite
        if not quotes:
            out.append(_r("quotes_in_excerpt", False, "minor", "no quotes on the page", "plan.quotes"))
        else:
            out.append(_r("quotes_in_excerpt", not bad, "major", bad[:3] or f"{len(quotes)} quotes verified", "plan.quotes"))
    else:
        out.append(_r("quotes_in_excerpt", None, "major", "no excerpt in case.json", "plan.quotes"))
    return out


def _n_inputs(controls: list) -> int:
    """Number of things a learner can change: a list or matrix with several entries counts each entry."""
    n = 0
    for c in controls:
        d = c.get("default")
        if c.get("kind") in ("vector", "prob", "matrix") and isinstance(d, list):
            n += sum(len(r) if isinstance(r, list) else 1 for r in d)
        else:
            n += 1
    return n


def _text(s) -> str:
    """Visible text of an HTML-lite string."""
    import html as h
    return h.unescape(re.sub(r"<[^>]+>", "", str(s or ""))).strip()


# ---------------------------------------------------------------- numeric helpers

def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _finite(v) -> bool:
    """True if v is a finite number, a string/bool, or nested lists of those."""
    if v is None:
        return False
    if _is_num(v):
        return math.isfinite(v)
    if isinstance(v, (str, bool)):
        return True
    if isinstance(v, list):
        return all(_finite(x) for x in v)
    if isinstance(v, dict):
        return all(_finite(x) for x in v.values())
    return False


def get_path(path, outputs: dict, state: dict):
    """Same lookup as runtime.js get(): 'outputs.k.0', 'state.id', or a bare key (outputs first)."""
    if not isinstance(path, str):
        return path
    parts = path.split(".")
    if parts[0] in ("outputs", "state"):
        cur, parts = (outputs if parts[0] == "outputs" else state), parts[1:]
    else:
        cur = outputs if parts[0] in outputs else state
    for p in parts:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        elif isinstance(cur, list) and p.isdigit() and int(p) < len(cur):
            cur = cur[int(p)]
        else:
            return None
    return cur


class Runner:
    """Runs compute_js and the plan's reference in QuickJS."""

    def __init__(self, compute_js: str, plan: dict):
        self.compute_js = compute_js
        self.ref_src = plan.get("reference_js") or ""
        call = plan.get("reference_call") or {"name": "reference", "params": ["state"]}
        self.ref_name, self.ref_params = call["name"], call["params"]

    def compute(self, state):
        return call_fn(self.compute_js, "compute", state, time_limit=2.0)

    def reference(self, state):
        args = [state] if self.ref_params == ["state"] else [state.get(p) for p in self.ref_params]
        return call_fn(self.ref_src, self.ref_name, *args, time_limit=2.0)


def defaults_of(plan: dict) -> dict:
    return {s["id"]: s.get("default") for s in plan.get("state", [])}


def _bounds(s):
    lo = s.get("min") if _is_num(s.get("min")) else -3
    hi = s.get("max") if _is_num(s.get("max")) else 3
    return (lo, hi) if lo <= hi else (hi, lo)


def _snap(x, s):
    """Round to the control's step grid (a step count of 1 must stay a whole number)."""
    step = s.get("step")
    if not (_is_num(step) and step > 0):
        return x
    lo = s.get("min") if _is_num(s.get("min")) else 0
    y = lo + round((x - lo) / step) * step
    return round(y, 10)


def _map_values(v, f):
    if isinstance(v, list):
        return [_map_values(x, f) for x in v]
    return f(v) if _is_num(v) else v


def perturbations(plan: dict, prob_ids: set) -> list[tuple[str, dict]]:
    """Generic input variations: extremes, zeros, ties, seeded random, flipped toggles."""
    base = defaults_of(plan)
    rnd = random.Random(SEED)
    cases = []

    def variant(name, f_num, f_bool=None, f_choice=None):
        st = json.loads(json.dumps(base))
        for s in plan.get("state", []):
            sid, kind, lo_hi = s["id"], s.get("kind"), _bounds(s)
            v = st.get(sid)
            if kind in ("number", "vector", "matrix"):
                v = _map_values(v, lambda x: _snap(f_num(x, *lo_hi), s))
                if sid in prob_ids and isinstance(v, list):
                    tot = sum(x for x in v if _is_num(x) and x > 0)
                    v = [max(0, x) / tot for x in v] if tot > 0 else [1 / len(v)] * len(v)
                st[sid] = v
            elif kind == "bool" and f_bool:
                st[sid] = f_bool(v)
            elif kind == "choice" and f_choice:
                st[sid] = f_choice(v, s.get("options") or [v])
        cases.append((name, st))

    variant("max", lambda x, lo, hi: hi)
    variant("min", lambda x, lo, hi: lo)
    variant("zeros", lambda x, lo, hi: 0 if lo <= 0 <= hi else lo)
    variant("ties", lambda x, lo, hi: 1 if lo <= 1 <= hi else lo)
    variant("random", lambda x, lo, hi: round(rnd.uniform(lo, hi), 2),
            lambda b: not b, lambda v, opts: opts[-1])
    variant("random2", lambda x, lo, hi: round(rnd.uniform(lo, hi), 2))
    return cases


def nudge(s: dict, v):
    """A small valid change to one control's value."""
    kind, (lo, hi) = s.get("kind"), _bounds(s)
    step = s.get("step") if _is_num(s.get("step")) and s.get("step") > 0 else (hi - lo) / 10 or 1

    def bump(x):
        y = x + step if x + step <= hi else x - step
        return y if y != x else x + 1

    if kind == "bool":
        return not v
    if kind == "choice":
        opts = s.get("options") or []
        return next((o for o in opts if o != v), v)
    if kind == "number" and _is_num(v):
        return bump(v)
    if kind == "vector" and isinstance(v, list) and v:
        return [bump(v[0])] + v[1:]
    if kind == "matrix" and isinstance(v, list) and v and isinstance(v[0], list) and v[0]:
        w = json.loads(json.dumps(v))
        w[0][0] = bump(w[0][0])
        return w
    return v


# ---------------------------------------------------------------- numeric checks

def numeric_checks(spec: dict, plan: dict, compute_js: str) -> list[CheckResult]:
    if not ENGINE:
        return [_r("numeric_checks", None, "critical", "JavaScript engine not available", "compute_js")]
    out = []
    run = Runner(compute_js, plan)
    keys = [o["key"] for o in plan.get("outputs", [])]
    invariants = plan.get("invariants") or []
    base = defaults_of(plan)

    # 1. runs on the defaults, returns every planned output, all finite
    try:
        r0 = run.compute(base)
    except JSError as e:
        return [_r("compute_runs_on_defaults", False, "critical", str(e), "compute_js")]
    outs = (r0 or {}).get("outputs") or {}
    out.append(_r("compute_runs_on_defaults", isinstance(outs, dict), "critical", "ok", "compute_js"))
    missing = [k for k in keys if k not in outs]
    out.append(_r("outputs_present", not missing, "major", missing or keys, "compute_js"))
    bad = [k for k in keys if k in outs and not _finite(outs[k])]
    out.append(_r("outputs_finite_on_defaults", not bad, "major", bad or "ok", "compute_js"))

    # 2. pre-registered tests: compute vs the reference answer key (and hand anchors)
    for t in plan.get("tests", []):
        st = dict(base, **(t.get("overrides") or {}))
        exp = dict(t.get("expected") or {})
        exp.update(t.get("anchors") or {})
        if not exp:
            out.append(_r(f"test:{t.get('name')}", None, "major", "no expected values", "plan.tests"))
            continue
        try:
            got = (run.compute(st) or {}).get("outputs") or {}
        except JSError as e:
            out.append(_r(f"test:{t.get('name')}", False, "major", f"compute threw: {e}", "compute_js"))
            continue
        wrong = {k: {"expected": v, "got": got.get(k)} for k, v in exp.items() if not close(got.get(k), v, t.get("tol", 1e-6))}
        target = "compute_js"
        if wrong and invariants:
            # if the reference itself breaks an invariant here but compute does not, the answer key is suspect
            ref_ok = all(_inv(i, t.get("expected") or {}, st) for i in invariants)
            cmp_ok = all(_inv(i, got, st) for i in invariants)
            if not ref_ok and cmp_ok:
                target = "plan.reference"
        out.append(_r(f"test:{t.get('name')}", not wrong, "major", _short(wrong) or "matches", target,
                      expected_vs_got=wrong))

    # 3. differential + invariants on generic variations of the inputs
    prob_ids = {c["id"] for c in spec.get("controls", []) if c.get("kind") == "prob"}
    disagree, inv_fail, crashes = [], [], []
    for name, st in perturbations(plan, prob_ids):
        try:
            ref = run.reference(st)
        except JSError:
            ref = None  # input outside the reference's domain: nothing to compare
        try:
            got = (run.compute(st) or {}).get("outputs") or {}
        except JSError as e:
            if ref is not None and all(_finite(ref.get(k)) for k in keys if k in ref):
                crashes.append(f"{name}: {str(e)[:80]}")
            continue
        if ref is not None:
            diff = [k for k in keys if k in ref and _finite(ref[k]) and not close(got.get(k), ref[k], 1e-6)]
            if diff:
                disagree.append(f"{name}: {', '.join(diff)}")
        for i in invariants:
            if not _inv(i, got, st) and (ref is None or _inv(i, ref, st)):
                inv_fail.append(f"{i.get('name')} @ {name}")
    out.append(_r("compute_matches_reference_on_variations", not disagree, "major", disagree[:5] or "ok", "compute_js"))
    out.append(_r("invariants_hold_on_variations", not inv_fail, "major", inv_fail[:5] or "ok", "compute_js"))
    out.append(_r("no_crash_on_valid_inputs", not crashes, "major", crashes[:5] or "ok", "compute_js"))

    # 4. every control changes something (no dead controls)
    dead = []
    full0 = _signature(r0)
    for s in plan.get("state", []):
        st = dict(base)
        st[s["id"]] = nudge(s, base.get(s["id"]))
        try:
            if _signature(run.compute(st)) == full0:
                dead.append(s["id"])
        except JSError:
            pass  # throwing is a visible reaction (error box), not a dead control
    out.append(_r("controls_have_effect", not dead, "major", dead or "every control changes the result", "compute_js"))

    # 5. every visual points at data that exists
    out.append(_visual_refs(spec, outs, base))
    return out


def _inv(inv: dict, outputs: dict, state: dict) -> bool:
    try:
        return eval_bool(inv.get("js", "false"), outputs, state)
    except JSError:
        return False


def _signature(result) -> str:
    r = result or {}
    return json.dumps([r.get("outputs"), r.get("intermediates")], sort_keys=True, default=str)


def _short(wrong: dict) -> str:
    return "; ".join(f"{k}: expected {json.dumps(v['expected'])[:60]} got {json.dumps(v['got'])[:60]}"
                     for k, v in wrong.items())


def _visual_refs(spec: dict, outputs: dict, state: dict) -> CheckResult:
    problems = []

    def need(path, where, shape=None):
        if not isinstance(path, str):
            return
        v = get_path(path, outputs, state)
        if v is None:
            problems.append(f"{where}: '{path}' not found")
        elif shape == "list" and not isinstance(v, list):
            problems.append(f"{where}: '{path}' is not a list")
        elif shape == "2d" and not (isinstance(v, list) and (not v or isinstance(v[0], list))):
            problems.append(f"{where}: '{path}' is not a 2-D array")

    for i, v in enumerate(spec.get("visuals") or []):
        w = f"visual {i + 1} ({v.get('kind')})"
        k = v.get("kind")
        if k in ("bar", "line"):
            for s in v.get("series") or []:
                need(s.get("data"), w, "list")
            if not v.get("series"):
                need(v.get("data"), w, "list")
            need(v.get("labels"), w, "list")
            if k == "line":
                need(v.get("x"), w, "list")
        elif k in ("heatmap", "matrix"):
            need(v.get("data"), w, "list")
            need(v.get("row_labels"), w, "list")
            need(v.get("col_labels"), w, "list")
        elif k == "svg":
            need(v.get("items_from"), w, "list")
            for it in v.get("items") or []:
                for key in ("x", "y", "w", "h", "r", "x2", "y2"):
                    m = re.match(r"^\{?([A-Za-z_][\w.]*)\}?$", str(it.get(key, ""))) if isinstance(it.get(key), str) else None
                    if m:
                        need(m.group(1), w)
        for text in (v.get("title"), v.get("caption")):
            for m in re.finditer(r"\{([A-Za-z0-9_.]+)(?::\d)?\}", str(text or "")):
                need(m.group(1), w + " caption")
    return _r("visual_data_refs", not problems, "major", problems[:6] or "all references resolve", "visuals")


# ---------------------------------------------------------------- entry point

def run_checks(case, plan: dict, built: dict, html: str, trace=None) -> list[CheckResult]:
    results = static_checks(case, built["spec"], html) + numeric_checks(built["spec"], plan, built["compute_js"])
    if trace is not None:
        for c in results:
            ev = {"severity": c.severity, "target": c.target}
            if c.data:
                ev.update(c.data)
            trace.check(c.name, bool(c.passed), c.detail, stage="check", skipped=c.passed is None, **ev)
    return results


def score(results: list[CheckResult]) -> tuple[int, int, int]:
    """(critical, major, minor) failure counts. Lower is better."""
    f = [c for c in results if c.passed is False]
    return (sum(c.severity == "critical" for c in f), sum(c.severity == "major" for c in f),
            sum(c.severity == "minor" for c in f))
