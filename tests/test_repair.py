import json
from pathlib import Path

import pytest

import agent
import p2p.build as b
from p2p import llm

FX = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).resolve().parents[1]
PLAN = json.loads((FX / "plan_attention.json").read_text(encoding="utf-8"))
FAKE = json.loads((FX / "build_attention.json").read_text(encoding="utf-8"))
CASE = str(ROOT / "cases" / "a_attention.json")


def run_agent(tmp_path, monkeypatch, *extra, build_reply=FAKE, repair_replies=(), build_error=None):
    """Run agent.main with fake model replies. Returns (exit code, trace events, purposes of model calls)."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    calls = []
    replies = list(repair_replies)

    def fake_chat(messages, *, purpose, budget, trace, **kw):
        calls.append(purpose)
        budget.record({"prompt_tokens": 100, "completion_tokens": 50})
        if purpose.startswith("build"):
            if build_error:
                raise build_error
            return json.loads(json.dumps(build_reply)), {}
        return replies.pop(0), {}

    monkeypatch.setattr(agent, "make_plan", lambda case, **k: (json.loads(json.dumps(PLAN)), []))
    monkeypatch.setattr(agent, "build", lambda case, plan, **k: b.build(case, plan, chat=fake_chat, **k))
    import p2p.repair as r
    orig = r.repair_loop
    monkeypatch.setattr(agent, "repair_loop", lambda *a, **k: orig(*a, chat=fake_chat, **k))
    out = tmp_path / "out"
    code = agent.main(["--input", CASE, "--output", str(out), "--model", "m", *extra])
    events = [json.loads(l) for l in (out / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    return code, events, calls, out


def test_healthy_run_makes_no_repair_call(tmp_path, monkeypatch):
    code, events, calls, out = run_agent(tmp_path, monkeypatch)
    assert code == 0 and calls == ["build"]
    assert not [e for e in events if e["action"] == "revision"]
    assert (out / "index.html").exists() and events[-1]["stage"] == "summary"


def test_injected_compute_fault_is_repaired(tmp_path, monkeypatch):
    patch = {"compute_js": FAKE["compute_js"], "reason": "removed the 1.5x scaling wrapper"}
    code, events, calls, out = run_agent(tmp_path, monkeypatch, "--inject-fault", "compute", repair_replies=[patch])
    assert code == 0 and calls == ["build", "repair_1"]
    rev = [e for e in events if e["action"] == "revision"]
    assert len(rev) == 1 and rev[0]["before"][1] > 0 and rev[0]["after"] == [0, 0, 0]
    assert rev[0]["targets"] == ["compute_js"] and "1.5x" in rev[0]["reason"]
    written = [e for e in events if e["action"] == "write_page"][-1]
    assert written["version"] == "v1" and written["failed"] == []
    assert "1.5" not in (out / "build.json").read_text(encoding="utf-8")


def test_repair_that_makes_things_worse_is_not_kept(tmp_path, monkeypatch):
    bad = {"compute_js": "function compute(state) { throw new Error('oops'); }", "reason": "bad"}
    code, events, calls, _ = run_agent(tmp_path, monkeypatch, "--inject-fault", "visuals", repair_replies=[bad, bad])
    written = [e for e in events if e["action"] == "write_page"][-1]
    assert written["version"] == "v0"           # the original (only visuals broken) beats a crashing compute
    assert written["critical"] == 0 and code == 0
    assert calls.count("repair_1") == 1 and len(calls) <= 3


def test_no_repair_flag(tmp_path, monkeypatch):
    code, events, calls, _ = run_agent(tmp_path, monkeypatch, "--inject-fault", "compute", "--no-repair")
    assert calls == ["build"] and code == 0  # major failures only: page still written, exit 0
    assert [e for e in events if e["action"] == "write_page"][-1]["major"] > 0


def test_build_failure_falls_back_to_reference_page(tmp_path, monkeypatch):
    code, events, calls, out = run_agent(tmp_path, monkeypatch, build_error=llm.Truncated("cut", "", {}))
    assert any(e["action"] == "fallback_page" for e in events)
    written = [e for e in events if e["action"] == "write_page"][-1]
    assert written["critical"] == 0 and code == 0
    page = (out / "index.html").read_text(encoding="utf-8")
    assert "function compute" in page and "reference" in page


def test_watchdog_writes_best_page_and_summary(tmp_path, monkeypatch):
    from p2p.budget import Budget
    from p2p.trace import Trace

    trace = Trace(tmp_path)
    keeper = agent.Keeper(tmp_path, trace, Budget())

    class V:
        html, label, score, failures = "<html>best</html>", "v0", (0, 1, 0), []

    keeper.offer(V())
    monkeypatch.setattr(agent.os, "_exit", lambda code: (_ for _ in ()).throw(SystemExit(code)))
    with pytest.raises(SystemExit) as e:
        keeper.timeout()
    assert e.value.code == 0
    assert (tmp_path / "index.html").read_text(encoding="utf-8") == "<html>best</html>"
    lines = [json.loads(l) for l in (tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [l["action"] for l in lines] == ["timeout", "write_page", "finish"]
