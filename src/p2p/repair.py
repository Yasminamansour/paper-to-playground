"""Targeted repair: fix only what failed, keep every version, pick the best.

Order of fixes per round:
1. Free fixes in Python (no tokens): drop quotes that are not in the excerpt.
2. One model call that receives ONLY the failing checks plus the fields to change, and
   returns a JSON patch with just those fields.
"""
from __future__ import annotations

import json

from . import llm as llm_mod
from .build import assemble_build, plan_brief, validate_build
from .checks import run_checks, score
from .plan import quotes_in_excerpt

REPAIR_MAX_TOKENS = 3000
MIN_TIME_FOR_REPAIR = 90  # seconds left before the soft deadline

# which build fields a failed check's target maps to
PATCHABLE = {
    "compute_js": ["compute_js"],
    "visuals": ["visuals"],
    "text": ["title", "idea", "playground_intro"],
    "page": ["title", "idea", "playground_intro", "visuals"],
    "plan.reference": ["compute_js"],
}

REPAIR_SYSTEM = """You fix one part of a generated interactive teaching page.
You get failing automatic checks and the current content of the fields involved.
Return ONE JSON object containing ONLY the fields you change, with their complete new values:
  "compute_js": full source of function compute(state) (same contract: return {outputs, intermediates};
                outputs keep every planned key and match REFERENCE exactly),
  "visuals": the complete visuals list, "idea": {"what","equation","why"}, "title", "playground_intro",
  "explorations": [{"title","change","observe","why","preset": {control_id: value}}] (exactly 2),
  "limitation": {"kind","text"}.
Also give "reason": one short line saying what you changed. No other text.
Rules: plain ES2017, no DOM, network, timers, randomness or globals. Text is HTML-lite (<b> <i> <sub> <sup> <code> <br>), no links.
A data reference in a visual is an output key, "outputs.key.0" or "state.id"."""


class Version:
    def __init__(self, label: str, plan: dict, built: dict, html: str, results: list):
        self.label, self.plan, self.built, self.html, self.results = label, plan, built, html, results
        self.score = score(results)

    @property
    def failures(self):
        return [r for r in self.results if r.passed is False]

    def needs_repair(self) -> bool:
        return any(r.severity in ("critical", "major") for r in self.failures)


def evaluate(label, case, plan, built, assemble, trace) -> Version:
    html = assemble(built["spec"], built["page_js"])
    return Version(label, plan, built, html, run_checks(case, plan, built, html, trace))


def free_fixes(case, plan: dict, failures) -> tuple[dict, list[str]]:
    """Fixes that need no model call. Returns (new_plan, what_changed)."""
    changed = []
    plan = json.loads(json.dumps(plan))
    names = {f.name for f in failures}
    if "quotes_in_excerpt" in names and case.excerpt:
        found, missing = quotes_in_excerpt(plan.get("grounding_quotes"), case.excerpt)
        if missing:
            plan["grounding_quotes"] = found
            changed.append(f"dropped {len(missing)} quote(s) not found in the excerpt")
    return plan, changed


def _failure_report(failures) -> list[dict]:
    rep = []
    for f in failures:
        if f.severity not in ("critical", "major"):
            continue
        item = {"check": f.name, "detail": f.detail}
        if f.data.get("expected_vs_got"):
            item["expected_vs_got"] = f.data["expected_vs_got"]
        rep.append(item)
    return rep[:8]


def repair_request(plan: dict, raw: dict, failures) -> tuple[list, list[str]]:
    targets = []
    for f in failures:
        if f.severity in ("critical", "major"):
            for t in PATCHABLE.get(f.target, []):
                if t not in targets:
                    targets.append(t)
    if any(f.name == "required_sections" for f in failures):
        targets += [t for t in ("explorations", "limitation") if t not in targets]
    current = {t: raw.get(t) for t in targets if t in raw}
    if "explorations" in targets:
        current["explorations"] = plan.get("explorations")
        current["limitation"] = plan.get("limitation")
    brief = plan_brief(plan)
    user = ("FAILING CHECKS: " + json.dumps(_failure_report(failures), ensure_ascii=False)
            + "\nCURRENT: " + json.dumps(current, ensure_ascii=False)
            + "\nPLAN: " + json.dumps({k: brief[k] for k in ("state", "outputs", "must_show_intermediates", "REFERENCE")},
                                      separators=(",", ":"), ensure_ascii=False)
            + "\nINVARIANTS: " + json.dumps([i.get("js") for i in plan.get("invariants", [])], ensure_ascii=False)
            + "\nReturn the JSON patch.")
    return [{"role": "system", "content": REPAIR_SYSTEM}, {"role": "user", "content": user}], targets


def apply_patch(plan: dict, raw: dict, patch: dict) -> tuple[dict, dict, list[str]]:
    raw = json.loads(json.dumps(raw))
    plan = json.loads(json.dumps(plan))
    changed = []
    for k in ("compute_js", "visuals", "title", "idea", "playground_intro", "controls"):
        if k in patch and patch[k] not in (None, "", []):
            raw[k] = patch[k]
            changed.append(k)
    for k in ("explorations", "limitation"):
        if k in patch and patch[k]:
            plan[k] = patch[k]
            changed.append(k)
    return plan, raw, changed


def repair_loop(case, plan, built, *, model, budget, trace, assemble, chat=llm_mod.chat,
                max_rounds: int = 2, on_version=None) -> Version:
    """Check, then repair up to max_rounds times while it helps and the budget allows.
    Returns the best version seen. on_version(v) is called for every version (for the watchdog)."""
    best = evaluate("v0", case, plan, built, assemble, trace)
    if on_version:
        on_version(best)
    current = best
    for rnd in range(1, max_rounds + 1):
        if not current.needs_repair():
            break
        before = current.score
        # 1. free fixes
        new_plan, free = free_fixes(case, current.plan, current.failures)
        cand = current
        if free:
            cand = evaluate(f"v{rnd}-free", case, new_plan, assemble_build(case, new_plan, current.built["raw"]),
                            assemble, trace)
            trace.revision(rnd, ["plan.grounding_quotes"], "; ".join(free), stage="repair",
                           before=list(before), after=list(cand.score))
            if on_version:
                on_version(cand)
            if cand.score < best.score:
                best = cand
            if not cand.needs_repair():
                current = cand
                break
        # 2. one model call for what is still failing
        if not budget.can_call(REPAIR_MAX_TOKENS) or budget.time_left() < MIN_TIME_FOR_REPAIR:
            trace.event("repair", "stop", "skip", reason="budget or time too low for another repair",
                        **{k: v for k, v in budget.totals().items() if k in ("calls", "completion_tokens")})
            break
        messages, targets = repair_request(cand.plan, cand.built["raw"], cand.failures)
        if not targets:
            trace.event("repair", "stop", "skip", reason="failing checks have no patchable target",
                        failed=[f.name for f in cand.failures])
            break
        try:
            patch, _ = chat(messages, model=model, max_tokens=REPAIR_MAX_TOKENS, purpose=f"repair_{rnd}",
                            schema=llm_mod.ANY_JSON, budget=budget, trace=trace, stage="repair")
        except llm_mod.LLMError as e:
            trace.event("repair", "repair_call", "fail", error=f"{type(e).__name__}: {e}")
            break
        if not isinstance(patch, dict):
            trace.event("repair", "repair_call", "fail", error="patch is not a JSON object")
            break
        new_plan, raw, changed = apply_patch(cand.plan, cand.built["raw"], patch)
        problems = validate_build(raw)
        if any("compute_js" in p for p in problems):
            trace.revision(rnd, changed, f"patch rejected: {problems}", stage="repair",
                           before=list(cand.score), after=list(cand.score))
            current = cand
            continue
        nxt = evaluate(f"v{rnd}", case, new_plan, assemble_build(case, new_plan, raw), assemble, trace)
        trace.revision(rnd, changed, str(patch.get("reason", ""))[:200], stage="repair",
                       before=list(cand.score), after=list(nxt.score))
        if on_version:
            on_version(nxt)
        if nxt.score < best.score:
            best = nxt
        current = nxt if nxt.score <= cand.score else cand
    trace.event("repair", "best_version", "info", version=best.label, score=list(best.score))
    return best
