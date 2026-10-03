"""PLAN call: one model request that fixes the concept, state and the answer key (tests)
before any page code exists. Python then validates and normalizes the result."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from . import llm as llm_mod
from .jsengine import JSError, call_fn, close, compiles, eval_bool, eval_js
from .prompts import PLAN_SYSTEM, plan_user
from .schemas import PLAN_SCHEMA

PLAN_MAX_TOKENS = 2000
PLAN_RETRY_MAX_TOKENS = 3200
SHORTER = ("Your previous answer was cut off at the token limit. Answer again, much shorter: "
           "strings under 10 words, 4 tests, 1 invariant, at most 4 symbols.")
ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,31}$")


def _loads(s, where, issues):
    try:
        return json.loads(s) if isinstance(s, str) else s
    except (json.JSONDecodeError, TypeError):
        issues.append(f"{where}: not valid JSON: {str(s)[:60]}")
        return None


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _num_array(v):
    if not isinstance(v, list) or not v:
        return False
    return all(_is_num(x) or _num_array(x) for x in v)


def _kind_ok(kind, v):
    if kind == "number":
        return _is_num(v)
    if kind == "vector":
        return isinstance(v, list) and len(v) > 0 and all(_is_num(x) for x in v)
    if kind == "matrix":
        return (isinstance(v, list) and len(v) > 0 and all(isinstance(r, list) and r for r in v)
                and len({len(r) for r in v}) == 1 and all(_is_num(x) for r in v for x in r))
    if kind == "bool":
        return isinstance(v, bool)
    if kind == "choice":
        return isinstance(v, str)
    return False


def normalize(raw: dict) -> tuple[dict, list[str]]:
    """Parse *_json fields, check structure. Returns (plan, issues). Issues are not fatal here;
    later stages decide what to do with them."""
    issues: list[str] = []
    p = json.loads(json.dumps(raw))  # deep copy

    ids = {}
    for i, s in enumerate(p.get("state", [])):
        sid = s.get("id", "")
        if not ID_RE.match(sid):
            issues.append(f"state[{i}].id '{sid}' is not a simple identifier")
        if sid in ids:
            issues.append(f"state id '{sid}' is repeated")
        s["default"] = _loads(s.pop("default_json", None), f"state.{sid}.default_json", issues)
        if s["default"] is not None and not _kind_ok(s.get("kind"), s["default"]):
            issues.append(f"state.{sid}: default does not match kind {s.get('kind')}")
        if s.get("kind") == "choice" and s.get("default") not in (s.get("options") or []):
            issues.append(f"state.{sid}: default is not one of the options")
        ids[sid] = s
    if not 1 <= len(ids) <= 8:
        issues.append(f"state has {len(ids)} entries (want 1 to 8)")

    out_keys = {o.get("key") for o in p.get("outputs", [])}

    def kv(items, where):
        res = {}
        for it in items or []:
            k = it.get("id")
            if k not in ids:
                issues.append(f"{where}: unknown control '{k}'")
                continue
            res[k] = _loads(it.get("value_json"), f"{where}.{k}", issues)
        return res

    for i, t in enumerate(p.get("tests", [])):
        where = f"tests[{i}] '{t.get('name', '')}'"
        t["overrides"] = kv(t.get("overrides"), where)
        anchors, anchors_js = {}, {}
        for e in t.get("anchors") or []:
            k = e.get("key")
            if k not in out_keys:
                issues.append(f"{where}: anchor for unknown output '{k}'")
                continue
            js = str(e.get("value_js", ""))
            anchors_js[k] = js
            try:
                v = eval_js(js)
            except JSError as err:
                issues.append(f"{where}: anchor '{k}' does not evaluate: {err}")
                continue
            if not (_is_num(v) or _num_array(v)):
                issues.append(f"{where}: anchor '{k}' must be a finite number or array of numbers")
                continue
            anchors[k] = v
        t["anchors"], t["anchors_js"] = anchors, anchors_js
        if not _is_num(t.get("tol")) or t["tol"] < 0:
            t["tol"] = 1e-6
    if len(p.get("tests", [])) < 3:
        issues.append(f"only {len(p.get('tests', []))} tests (want 4)")

    for i, x in enumerate(p.get("explorations", [])):
        x["preset"] = kv(x.get("preset"), f"explorations[{i}]")
    if len(p.get("explorations", [])) != 2:
        issues.append(f"{len(p.get('explorations', []))} explorations (want exactly 2)")

    for inv in p.get("invariants", []):
        js = inv.get("js", "")
        if re.search(r"\b(function|while|for|fetch|import|eval|require|XMLHttpRequest)\b", js):
            issues.append(f"invariant '{inv.get('name')}' must be a plain expression")
            continue
        err = compiles(f"function(out, state) {{ return ({js}); }}")
        if err:
            issues.append(f"invariant '{inv.get('name')}' does not parse: {err}")
    p["answer_key"] = answer_key(p, ids, out_keys, issues)
    return p, issues


def test_state(p: dict, test: dict) -> dict:
    """Full state for a test: defaults, then the test's overrides."""
    st = {s["id"]: s.get("default") for s in p.get("state", [])}
    st.update(test.get("overrides") or {})
    return st


def answer_key(p: dict, ids: dict, out_keys: set, issues: list) -> dict:
    """Run reference_js on every test. Expected values come from the reference; the model's
    hand anchors are cross-checked against it and kept only when they agree."""
    stats = {"reference_ok": False, "anchors_agree": 0, "anchors_suspect": [], "invariant_failures": []}
    src = p.get("reference_js") or ""
    if re.search(r"\b(fetch|import|require|XMLHttpRequest|document|window|Math\.random)\b", src):
        issues.append("reference_js uses a forbidden API")
        return stats
    m = re.search(r"function\s+([A-Za-z_$][\w$]*)\s*\(([^)]*)\)", src)
    if not m:
        issues.append("reference_js has no named function")
        return stats
    fname = m.group(1)
    params = [x.strip().split("=")[0].strip() for x in m.group(2).split(",") if x.strip()]
    err = compiles(src.strip().rstrip(";")) if src.strip().startswith("function") else None
    if err:
        issues.append(f"reference_js does not parse: {err}")
        return stats
    # call with the whole state, or, if every parameter names a control, with those values
    by_name = len(params) > 1 or (len(params) == 1 and params[0] in ids)
    if by_name and not all(x in ids for x in params):
        issues.append(f"reference_js parameters {params} do not match the controls")
        return stats
    p["reference_call"] = {"name": fname, "params": params if by_name else ["state"]}
    ok_all = True
    for t in p.get("tests", []):
        st = test_state(p, t)
        try:
            args = [st[x] for x in params] if by_name else [st]
            ref = call_fn(src, fname, *args)
        except JSError as e:
            issues.append(f"reference fails on test '{t.get('name')}': {e}")
            ok_all = False
            continue
        if not isinstance(ref, dict):
            issues.append("reference must return an object")
            ok_all = False
            continue
        missing = sorted(out_keys - set(ref))
        if missing:
            issues.append(f"reference does not return: {', '.join(missing)}")
        t["expected"] = {k: ref[k] for k in out_keys if k in ref}
        kept = {}
        for k, v in (t.get("anchors") or {}).items():
            if k in ref and close(ref[k], v, t["tol"]):
                kept[k] = v
                stats["anchors_agree"] += 1
            else:
                stats["anchors_suspect"].append(f"{t.get('name')}.{k}")
        t["anchors"] = kept
        for inv in p.get("invariants", []):
            try:
                good = eval_bool(inv.get("js", "false"), ref, st)
            except JSError:
                good = False
            if not good:
                stats["invariant_failures"].append(f"{inv.get('name')} @ {t.get('name')}")
    stats["reference_ok"] = ok_all and bool(p.get("tests"))
    return stats


def is_formula_like(q: str) -> bool:
    """Mostly math (often garbled by copying), not a sentence: fewer than 4 real words, or symbols with few words."""
    words = re.findall(r"[A-Za-z]{3,}", q)
    has_math = bool(re.search(r"[=∑Σ√∫^_]", q))
    return len(words) < 4 or (has_math and len(words) < 6)


def pick_quotes(excerpt: str, plan: dict, n: int = 2) -> list[str]:
    """Pick up to n real sentences from the excerpt (verbatim) that best match the concept."""
    key = set(w.lower() for w in re.findall(r"[A-Za-z]{4,}", " ".join(
        [plan.get("concept", ""), plan.get("why_it_matters", "")] + [o.get("meaning", "") for o in plan.get("outputs", [])])))
    sents = [x.strip() for x in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", excerpt)) if 40 <= len(x.strip()) <= 260]
    sents = [x for x in sents if not is_formula_like(x)]
    ranked = sorted(sents, key=lambda x: -len(key & set(w.lower() for w in re.findall(r"[A-Za-z]{4,}", x))))
    return [x for x in ranked[:n] if key & set(w.lower() for w in re.findall(r"[A-Za-z]{4,}", x))]


def quotes_in_excerpt(quotes, excerpt: str) -> tuple[list[str], list[str]]:
    """Split quotes into (found, missing) by exact substring match, ignoring whitespace runs."""
    norm = lambda s: re.sub(r"\s+", " ", s).strip()
    ex = norm(excerpt or "")
    found, missing = [], []
    for q in quotes or []:
        (found if ex and norm(q) and norm(q) in ex else missing).append(q)
    return found, missing


def _debug_dump(trace, name: str, text: str):
    """Dev only: with P2P_DEBUG=1, save a raw model reply next to the trace (never in the trace)."""
    if os.environ.get("P2P_DEBUG") == "1" and text:
        path = Path(trace.path).parent / "debug" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(text, encoding="utf-8")


def fatal_problems(plan: dict, issues: list[str]) -> list[str]:
    """Problems that make the plan useless as an answer key (worth one repair call)."""
    out = []
    if not plan.get("state"):
        out.append("state is empty")
    if len(plan.get("tests") or []) < 3:
        out.append(f"only {len(plan.get('tests') or [])} tests; give exactly 4")
    if not plan.get("answer_key", {}).get("reference_ok"):
        out += [i for i in issues if "reference" in i] or ["reference_js did not run on the tests"]
    out += [i for i in issues if "default" in i or "unknown control" in i]
    return out[:8]


def _call(chat, messages, *, model, budget, trace, purpose):
    """One plan request, with one longer retry if the answer was cut off."""
    try:
        return chat(messages, model=model, max_tokens=PLAN_MAX_TOKENS, purpose=purpose,
                    schema=PLAN_SCHEMA, budget=budget, trace=trace, stage="plan")[0]
    except llm_mod.Truncated as e:
        _debug_dump(trace, f"{purpose}_truncated.txt", e.text)
        trace.revision(1, ["plan"], f"truncated: {e}", stage="plan")
        messages = messages + [{"role": "user", "content": SHORTER}]
        return chat(messages, model=model, max_tokens=PLAN_RETRY_MAX_TOKENS, purpose=purpose + "_retry",
                    schema=PLAN_SCHEMA, budget=budget, trace=trace, stage="plan")[0]


def make_plan(case, *, model, budget, trace, chat=llm_mod.chat) -> tuple[dict, list[str]]:
    messages = [{"role": "system", "content": PLAN_SYSTEM},
                {"role": "user", "content": plan_user(case.context_block())}]
    raw = _call(chat, messages, model=model, budget=budget, trace=trace, purpose="plan")
    if not isinstance(raw, dict):
        raise llm_mod.BadJSON("plan is not a JSON object")
    plan, issues = normalize(raw)
    fatal = fatal_problems(plan, issues)
    if fatal and budget.can_call(PLAN_MAX_TOKENS):
        # targeted repair: show the model its own plan and exactly what is wrong
        trace.revision(2, ["plan"], "; ".join(fatal)[:300], stage="plan")
        fix = messages + [
            {"role": "assistant", "content": json.dumps(raw, separators=(",", ":"), ensure_ascii=False)},
            {"role": "user", "content": "Your plan has these problems:\n- " + "\n- ".join(fatal)
             + "\nReturn the complete corrected plan JSON."},
        ]
        try:
            raw2 = _call(chat, fix, model=model, budget=budget, trace=trace, purpose="plan_repair")
            plan2, issues2 = normalize(raw2) if isinstance(raw2, dict) else (None, None)
            if plan2 is not None and len(fatal_problems(plan2, issues2)) < len(fatal):
                plan, issues = plan2, issues2
                trace.event("plan", "repair_result", "ok", remaining=fatal_problems(plan2, issues2))
            else:
                trace.event("plan", "repair_result", "fail", detail="repair was not better; kept first plan")
        except llm_mod.LLMError as e:
            trace.event("plan", "repair_result", "fail", error=f"{type(e).__name__}: {e}")

    formula_like = [q for q in plan.get("grounding_quotes") or [] if is_formula_like(q)]
    if formula_like:
        plan["grounding_quotes"] = [q for q in plan["grounding_quotes"] if q not in formula_like]
        trace.event("plan", "drop_formula_quotes", "info", dropped=len(formula_like))
    found, missing = quotes_in_excerpt(plan.get("grounding_quotes"), case.excerpt)
    if missing:
        # keep only verifiable quotes; the page must not show invented "quotes"
        plan["grounding_quotes"] = found
    if case.excerpt and not plan["grounding_quotes"]:
        plan["grounding_quotes"] = pick_quotes(case.excerpt, plan)
        trace.event("plan", "pick_quotes", "info", picked=len(plan["grounding_quotes"]))
    bad_inv = sorted({f.split(" @ ")[0] for f in plan.get("answer_key", {}).get("invariant_failures", [])})
    if bad_inv:
        # an invariant that fails on the reference is wrong or too strict: never show it as a live FAIL
        plan["invariants"] = [i for i in plan.get("invariants", []) if i.get("name") not in bad_inv]
        trace.event("plan", "drop_invariants", "info", dropped=bad_inv)
    trace.check("plan_quotes_verbatim", not missing, f"{len(found)} found, {len(missing)} dropped",
                stage="plan", skipped=not case.excerpt)
    trace.check("plan_structure", not issues, issues[:8] or "ok", stage="plan")
    ak = plan.get("answer_key", {})
    trace.check("plan_reference_runs", bool(ak.get("reference_ok")), "reference_js evaluated on all tests", stage="plan")
    trace.check("plan_anchors_agree", not ak.get("anchors_suspect"),
                f"{ak.get('anchors_agree', 0)} agree; suspect (dropped): {ak.get('anchors_suspect') or 'none'}", stage="plan")
    trace.check("plan_invariants_hold_on_reference", not ak.get("invariant_failures"),
                ak.get("invariant_failures") or "ok", stage="plan")
    trace.event("plan", "plan_summary", "info", concept=plan.get("concept", "")[:120],
                n_state=len(plan.get("state", [])), n_tests=len(plan.get("tests", [])),
                n_invariants=len(plan.get("invariants", [])), equation=plan.get("source", {}).get("equation_label"))
    return plan, issues
