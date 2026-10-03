import json
from pathlib import Path

import pytest

from p2p.build import BuildError, build, checks_wrapper, make_controls, pretty_symbol
from p2p.budget import Budget
from p2p.case import load_case
from p2p.jsengine import call_fn
from p2p.trace import Trace

FX = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).resolve().parents[1]
PLAN = json.loads((FX / "plan_attention.json").read_text(encoding="utf-8"))
FAKE = json.loads((FX / "build_attention.json").read_text(encoding="utf-8"))


def run_build(tmp_path, reply):
    case = load_case(ROOT / "cases" / "a_attention.json")
    return build(case, PLAN, model="m", budget=Budget(), trace=Trace(tmp_path), chat=lambda m, **k: (reply, {}))


def test_pretty_symbol():
    assert pretty_symbol("d_k") == "d<sub>k</sub>"
    assert pretty_symbol("Q K^T / sqrt(d_k)") == "Q K<sup>T</sup> / √(d<sub>k</sub>)"
    assert pretty_symbol("x_{ij}") == "x<sub>ij</sub>"


def test_spec_filled_from_plan(tmp_path):
    r = run_build(tmp_path, FAKE)
    spec = r["spec"]
    assert [c["id"] for c in spec["controls"]] == [s["id"] for s in PLAN["state"]]
    assert spec["grounding"]["from_excerpt"] == PLAN["grounding_quotes"]
    assert spec["grounding"]["source_url"].startswith("https://arxiv.org")
    assert len(spec["explorations"]) == 2 and spec["explorations"][0]["preset"] == PLAN["explorations"][0]["preset"]
    assert spec["limitation"] == PLAN["limitation"]


def test_widget_must_fit_kind():
    plan = {"state": [{"id": "p", "kind": "vector", "default": [0.5, 0.5]}, {"id": "a", "kind": "number", "default": 1, "min": 0, "max": 2}]}
    c = make_controls(plan, [{"id": "p", "widget": "matrix"}, {"id": "a", "widget": "toggle"}])
    assert c[0]["kind"] == "vector" and c[1]["kind"] == "slider"


def test_page_js_adds_invariant_checks():
    js = checks_wrapper("function compute(s){ return {outputs:{x: s.a}}; }",
                        [{"name": "x positive", "js": "out.x > 0"}, {"name": "broken", "js": "out.x >"}])
    r = call_fn(js + "\nfunction run(s){ return compute(s); }", "run", {"a": 2})
    assert r["checks"] == [{"label": "x positive", "pass": True, "detail": ""}]


def test_forbidden_api_rejected(tmp_path):
    bad = dict(FAKE, compute_js="function compute(s){ fetch('x'); return {outputs:{}}; }")
    with pytest.raises(BuildError, match="forbidden"):
        run_build(tmp_path, bad)
    bad = dict(FAKE, compute_js="function compute(s){ return {outputs:{}} ")
    with pytest.raises(BuildError, match="parse|not defined"):
        run_build(tmp_path, bad)


def test_agent_writes_page(tmp_path, monkeypatch):
    import agent
    import p2p.build as b
    import p2p.plan as p

    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    monkeypatch.setattr(agent, "make_plan", lambda case, **k: (PLAN, []))
    monkeypatch.setattr(agent, "build", lambda case, plan, **k: b.build(case, plan, chat=lambda m, **kk: (FAKE, {}), **k))
    out = tmp_path / "out"
    code = agent.main(["--input", str(ROOT / "cases" / "a_attention.json"), "--output", str(out), "--model", "m"])
    assert code == 0
    page = (out / "index.html").read_text(encoding="utf-8")
    assert "Scaled dot-product attention" in page and "function compute" in page
    last = json.loads((out / "trace.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert last["stage"] == "summary" and last["exit_code"] == 0
