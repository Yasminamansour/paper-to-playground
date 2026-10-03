import json

import pytest

from p2p import llm
from p2p.budget import Budget
from p2p.case import Case
from p2p.plan import PLAN_MAX_TOKENS, PLAN_RETRY_MAX_TOKENS, make_plan, normalize, quotes_in_excerpt
from p2p.schemas import PLAN_SCHEMA
from p2p.trace import Trace


def good_raw():
    kv = lambda i, v: {"id": i, "value_json": json.dumps(v)}
    ex = lambda k, v: {"key": k, "value_js": json.dumps(v)}
    ref = "function reference(state) { return { ys: state.xs.map(function (x) { return state.a * x; }) }; }"
    return {
        "concept": "line", "why_it_matters": "w", "audience_notes": "n",
        "source": {"paper_title": "T", "section_label": "S1", "equation_label": "Eq. 1", "equation_text": "y = a x + b"},
        "grounding_quotes": ["a line is   set by", "not in the excerpt"],
        "symbols": [{"symbol": "a", "meaning": "slope", "units": "-"}],
        "state": [
            {"id": "a", "kind": "number", "label": "a", "default_json": "1", "min": -3, "max": 3, "step": 0.1, "options": []},
            {"id": "xs", "kind": "vector", "label": "x", "default_json": "[1,2]", "min": None, "max": None, "step": None, "options": []},
        ],
        "outputs": [{"key": "ys", "meaning": "y values"}],
        "must_show_intermediates": ["ys"],
        "tests": [{"name": f"t{i}", "overrides": [kv("a", i)], "anchors": [ex("ys", [i, 2 * i])], "tol": 1e-9, "rationale": "r"}
                  for i in range(4)],
        "reference_js": ref,
        "invariants": [{"name": "len", "js": "out.ys.length === state.xs.length"}],
        "explorations": [{"title": "x", "change": "c", "observe": "o", "why": "w", "preset": [kv("a", 0)]}] * 2,
        "limitation": {"kind": "assumption", "text": "t"},
        "simplifications": ["toy numbers"],
        "visual_idea": "bar chart",
    }


def test_schema_is_strict_everywhere():
    def walk(s, path="root"):
        if s.get("type") == "object":
            assert s["additionalProperties"] is False, path
            assert set(s["required"]) == set(s["properties"]), path
            for k, v in s["properties"].items():
                walk(v, f"{path}.{k}")
        if s.get("type") == "array":
            walk(s["items"], path + "[]")
    walk(PLAN_SCHEMA)


def test_normalize_parses_values():
    p, issues = normalize(good_raw())
    assert issues == []
    assert p["state"][1]["default"] == [1, 2]
    assert p["tests"][2]["overrides"] == {"a": 2} and p["tests"][2]["anchors"] == {"ys": [2, 4]}
    assert p["tests"][2]["expected"] == {"ys": [2, 4]} and p["answer_key"]["reference_ok"]
    assert p["explorations"][0]["preset"] == {"a": 0}


def test_normalize_finds_problems():
    raw = good_raw()
    raw["state"][0]["default_json"] = "[1"            # bad json
    raw["state"][1]["default_json"] = "5"             # wrong kind
    raw["tests"] = raw["tests"][:2]                   # too few
    raw["tests"][0]["overrides"][0]["id"] = "nope"    # unknown control
    raw["tests"][1]["anchors"][0]["key"] = "zz"       # unknown output
    raw["invariants"] = [{"name": "f", "js": "(function(){return true})()"}, {"name": "p", "js": "out.ys.every(y =>"}]
    raw["explorations"] = raw["explorations"][:1]
    _, issues = normalize(raw)
    text = " | ".join(issues)
    for frag in ("not valid JSON", "does not match kind", "only 2 tests", "unknown control 'nope'",
                 "unknown output 'zz'", "must be a plain expression", "does not parse", "1 explorations"):
        assert frag in text, frag


def test_quotes_checked_against_excerpt():
    found, missing = quotes_in_excerpt(["a line is set by", "made up"], "Here a line\nis set by two numbers.")
    assert found == ["a line is set by"] and missing == ["made up"]


def test_make_plan_drops_invented_quotes_and_traces(tmp_path):
    trace, budget, seen = Trace(tmp_path), Budget(), {}

    def fake_chat(messages, **kw):
        seen.update(kw)
        seen["messages"] = messages
        return good_raw(), {"completion_tokens": 900}

    case = Case("u", "explain lines", "students", extra={"excerpt": "Here a line is set by two numbers."})
    plan, issues = make_plan(case, model="m", budget=budget, trace=trace, chat=fake_chat)
    assert plan["grounding_quotes"] == ["a line is   set by"]
    assert seen["max_tokens"] == PLAN_MAX_TOKENS and seen["schema"] is PLAN_SCHEMA and seen["purpose"] == "plan"
    assert "EXCERPT" in seen["messages"][1]["content"]
    trace.close()
    lines = [json.loads(l) for l in (tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    checks = {e["action"]: e["result"] for e in lines if e["action"].startswith("check:")}
    assert checks == {"check:plan_quotes_verbatim": "fail", "check:plan_structure": "ok",
                      "check:plan_reference_runs": "ok", "check:plan_anchors_agree": "ok",
                      "check:plan_invariants_hold_on_reference": "ok"}


def test_make_plan_rejects_non_object(tmp_path):
    with pytest.raises(llm.BadJSON):
        make_plan(Case("u", "f", "a"), model="m", budget=Budget(), trace=Trace(tmp_path),
                  chat=lambda m, **k: (["not", "an", "object"], {}))


def test_truncated_plan_is_retried_once(tmp_path):
    calls = []

    def fake_chat(messages, **kw):
        calls.append(kw["max_tokens"])
        if len(calls) == 1:
            raise llm.Truncated("cut", "", {})
        assert "cut off" in messages[-1]["content"]
        return good_raw(), {}

    trace = Trace(tmp_path)
    plan, _ = make_plan(Case("u", "f", "a"), model="m", budget=Budget(), trace=trace, chat=fake_chat)
    assert calls == [PLAN_MAX_TOKENS, PLAN_RETRY_MAX_TOKENS] and plan["concept"] == "line"
    trace.close()
    assert '"action": "revision"' in (tmp_path / "trace.jsonl").read_text(encoding="utf-8")


def test_answer_key_comes_from_reference_and_bad_anchors_are_dropped():
    raw = good_raw()
    raw["tests"][1]["anchors"] = [{"key": "ys", "value_js": "[5, 5]"}]            # wrong mental math
    raw["tests"][2]["anchors"] = [{"key": "ys", "value_js": "[1, 2"}]             # does not evaluate
    raw["tests"][3]["anchors"] = [{"key": "ys", "value_js": "[Math.sqrt(9), 2*3]"}]  # right: a=3, xs=[1,2]
    raw["invariants"] = [{"name": "arrow ok", "js": "out.ys.every(y => Number.isFinite(y))"},
                         {"name": "always false", "js": "out.ys[0] > 100"}]
    p, issues = normalize(raw)
    ak = p["answer_key"]
    assert p["tests"][1]["expected"] == {"ys": [1, 2]} and p["tests"][1]["anchors"] == {}
    assert p["tests"][3]["anchors"] == {"ys": [3, 6]}
    assert ak["anchors_suspect"] == ["t1.ys"] and ak["anchors_agree"] == 2
    assert len(ak["invariant_failures"]) == 4 and all(f.startswith("always false") for f in ak["invariant_failures"])
    assert any("does not evaluate" in i for i in issues)


def test_broken_reference_is_reported():
    raw = good_raw()
    raw["reference_js"] = "function reference(state) { return state.nope.x; }"
    p, issues = normalize(raw)
    assert not p["answer_key"]["reference_ok"] and any("reference fails" in i for i in issues)
    raw["reference_js"] = "fetch('x')"
    _, issues = normalize(raw)
    assert any("forbidden" in i for i in issues)




def test_reference_with_any_name_or_named_parameters():
    raw = good_raw()
    raw["reference_js"] = "function lineValues(a, xs) { return { ys: xs.map(function (x) { return a * x; }) }; }"
    p, issues = normalize(raw)
    assert p["answer_key"]["reference_ok"] and p["tests"][3]["expected"] == {"ys": [3, 6]}
    assert p["reference_call"] == {"name": "lineValues", "params": ["a", "xs"]}
    raw["reference_js"] = "function compute(s) { return { ys: s.xs.map(function (x) { return s.a * x; }) }; }"
    p, _ = normalize(raw)
    assert p["answer_key"]["reference_ok"] and p["reference_call"]["params"] == ["state"]
    raw["reference_js"] = "function f(a, zz) { return {}; }"
    _, issues = normalize(raw)
    assert any("do not match the controls" in i for i in issues)


def test_formula_quotes_are_dropped(tmp_path):
    raw = good_raw()
    raw["grounding_quotes"] = ["H = K n sum pi log pi", "a line is set by"]
    case = Case("u", "f", "a", extra={"excerpt": "H = K n sum pi log pi and a line is set by two numbers."})
    plan, _ = make_plan(case, model="m", budget=Budget(), trace=Trace(tmp_path), chat=lambda m, **k: (raw, {}))
    assert plan["grounding_quotes"] == ["a line is set by"]


def test_plan_with_no_tests_gets_one_repair(tmp_path):
    bad = good_raw()
    bad["tests"] = []
    replies = [bad, good_raw()]
    sent = []

    def fake_chat(messages, **kw):
        sent.append((kw["purpose"], messages[-1]["content"]))
        return replies.pop(0), {}

    trace = Trace(tmp_path)
    plan, _ = make_plan(Case("u", "f", "a"), model="m", budget=Budget(), trace=trace, chat=fake_chat)
    assert [p for p, _ in sent] == ["plan", "plan_repair"]
    assert "only 0 tests" in sent[1][1]
    assert len(plan["tests"]) == 4 and plan["answer_key"]["reference_ok"]
    trace.close()
    text = (tmp_path / "trace.jsonl").read_text(encoding="utf-8")
    assert '"action": "revision"' in text and '"action": "repair_result", "result": "ok"' in text


def test_good_plan_makes_one_call(tmp_path):
    calls = []
    make_plan(Case("u", "f", "a"), model="m", budget=Budget(), trace=Trace(tmp_path),
              chat=lambda m, **k: (calls.append(1), (good_raw(), {}))[1])
    assert len(calls) == 1
