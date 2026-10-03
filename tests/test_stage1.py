import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from p2p.budget import Budget
from p2p.case import MAX_FIELD_CHARS, CaseError, load_case
from p2p.trace import Trace, redact

ROOT = Path(__file__).resolve().parents[1]
FAKE_KEY = "sk-or-" + "v1-" + "abc123"  # built at runtime so the secret scan stays quiet


def write(tmp_path, obj, name="case.json"):
    p = tmp_path / name
    p.write_text(json.dumps(obj), encoding="utf-8")
    return p


def run_agent(*args):
    env = {k: v for k, v in os.environ.items() if k != "OPENROUTER_API_KEY"}  # never call the API in tests
    return subprocess.run([sys.executable, str(ROOT / "agent.py"), *args], capture_output=True, text=True, env=env)


# case loader
def test_missing_field_raises(tmp_path):
    with pytest.raises(CaseError, match="focus"):
        load_case(write(tmp_path, {"source_url": "u", "audience": "a"}))


def test_non_string_required_raises(tmp_path):
    with pytest.raises(CaseError):
        load_case(write(tmp_path, {"source_url": "u", "focus": 5, "audience": "a"}))


def test_bad_json_raises(tmp_path):
    p = tmp_path / "c.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(CaseError):
        load_case(p)


def test_extra_fields_preserved_and_excerpt_first(tmp_path):
    c = load_case(write(tmp_path, {"source_url": "u", "focus": "f", "audience": "a",
                                    "notes": "n", "excerpt": "E", "weird": 5, "nested": {"x": 1}}))
    assert list(c.extra) == ["notes", "excerpt", "weird", "nested"]
    assert c.extra["weird"] == "5" and c.extra["nested"] == '{"x": 1}'
    block = c.context_block()
    assert block.index("EXCERPT") < block.index("NOTES")


def test_bom_and_truncation(tmp_path):
    p = tmp_path / "c.json"
    obj = {"source_url": "u", "focus": "f", "audience": "a", "excerpt": "x" * (MAX_FIELD_CHARS + 50)}
    p.write_text("﻿" + json.dumps(obj), encoding="utf-8")
    c = load_case(p)
    assert c.truncated == ["excerpt"] and "truncated 50 chars" in c.excerpt


# trace
def test_redaction():
    out = redact({"Authorization": "Bearer x", "api_key": "y", "note": f"key {FAKE_KEY}",
                  "messages": [{"content": "hi"}], "reasoning": "secret", "n": 3})
    assert out == {"note": "[REDACTED]", "n": 3}


def test_trace_lines_are_valid_json(tmp_path):
    t = Trace(tmp_path)
    t.event("s", "a", "ok", detail=FAKE_KEY)
    t.llm_call(stage="plan", call_index=1, purpose="plan", model="m", ok=True, prompt_tokens=10,
               completion_tokens=5, prompt_text="hello")
    t.check("rows_sum_to_1", True)
    t.revision(1, ["compute_js"], "check failed")
    t.summary({"calls": 1}, ok=True, exit_code=0)
    t.close()
    text = (tmp_path / "trace.jsonl").read_text(encoding="utf-8")
    assert "sk-or-" not in text and "hello" not in text
    for line in text.splitlines():
        ev = json.loads(line)
        assert {"ts", "t", "stage", "action", "result"} <= ev.keys()


# budget
def test_budget_clamp_and_limits():
    b = Budget()
    assert b.clamp_max_tokens(7000) == 7000
    b.record({"prompt_tokens": 100, "completion_tokens": 24_000,
              "completion_tokens_details": {"reasoning_tokens": 300}})
    assert b.clamp_max_tokens(7000) == 2000
    assert b.totals()["reasoning_tokens"] == 300
    b.record(None, requested_max_tokens=2000)  # failed attempt, no usage: charge worst case
    assert b.remaining_completion() == 0 and not b.can_call(1000)


def test_budget_counts_every_attempt():
    b = Budget()
    for _ in range(8):
        assert b.can_call()
        b.record(None)
    assert not b.can_call()


# CLI
def test_cli_dry_run_ok(tmp_path):
    case = write(tmp_path, {"source_url": "u", "focus": "f", "audience": "a", "excerpt": "e", "weird": 5})
    out = tmp_path / "o"
    r = run_agent("--input", str(case), "--output", str(out), "--model", "m", "--dry-run")
    assert r.returncode == 0, r.stderr
    assert (out / "index.html").exists()
    lines = [json.loads(l) for l in (out / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    assert lines[-1]["stage"] == "summary" and lines[-1]["exit_code"] == 0


def test_cli_missing_focus_exits_2(tmp_path):
    case = write(tmp_path, {"source_url": "u", "audience": "a"})
    r = run_agent("--input", str(case), "--output", str(tmp_path / "o"), "--model", "m")
    assert r.returncode == 2


def test_cli_bad_usage_exits_2():
    assert run_agent("--input", "x").returncode == 2


def test_cli_without_key_exits_1(tmp_path):
    case = write(tmp_path, {"source_url": "u", "focus": "f", "audience": "a"})
    r = run_agent("--input", str(case), "--output", str(tmp_path / "o"), "--model", "m")
    assert r.returncode == 1
    assert not (tmp_path / "o" / "index.html").exists()
