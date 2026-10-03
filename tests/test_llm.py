import json

import pytest
import requests

from p2p import llm
from p2p.budget import Budget
from p2p.trace import Trace

FAKE_KEY = "sk-or-" + "v1-" + "testkey"
OK_USAGE = {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70,
            "completion_tokens_details": {"reasoning_tokens": 0},
            "prompt_tokens_details": {"cached_tokens": 0}}
CFG = {"reasoning": None, "json_mode": "schema", "require_params": True, "temperature": 0.2}
MSGS = [{"role": "user", "content": "secret prompt text"}]


class Resp:
    def __init__(self, status=200, data=None, headers=None):
        self.status_code, self._data, self.headers = status, data, headers or {}

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


class Session:
    def __init__(self, *items):
        self.items, self.bodies, self.headers = list(items), [], []

    def post(self, url, headers=None, json=None, timeout=None):
        self.bodies.append(json)
        self.headers.append(headers)
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def ok(content='{"sum": 42}', finish="stop", usage=OK_USAGE):
    return Resp(200, {"id": "gen-1", "provider": "P", "usage": usage,
                      "choices": [{"finish_reason": finish, "message": {"content": content}}]})


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_KEY)
    trace, budget, sleeps = Trace(tmp_path), Budget(), []

    def call(session, schema={"type": "object"}, max_tokens=500):
        return llm.chat(MSGS, model="m", max_tokens=max_tokens, purpose="t", schema=schema, budget=budget,
                        trace=trace, session=session, sleep=sleeps.append, cfg=CFG)

    def lines():
        trace._f.flush()
        return [json.loads(l) for l in (tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()]

    return call, budget, sleeps, lines


def test_success_parses_json_and_traces_usage(env):
    call, budget, _, lines = env
    s = Session(ok('```json\n{"sum": 42}\n```'))
    content, usage = call(s)
    assert content == {"sum": 42} and usage["completion_tokens"] == 20
    body = s.bodies[0]
    assert body["response_format"]["type"] == "json_schema" and body["provider"] == {"require_parameters": True}
    ev = [e for e in lines() if e["action"] == "llm_call"][0]
    assert ev["prompt_tokens"] == 50 and ev["generation_id"] == "gen-1" and ev["prompt_fp"]["chars"] > 0
    assert budget.calls == 1


def test_401_not_retried(env):
    call, budget, sleeps, _ = env
    s = Session(Resp(401, {"error": {"code": 401, "message": "bad key"}}), ok())
    with pytest.raises(llm.LLMError, match="401"):
        call(s)
    assert budget.calls == 1 and sleeps == [] and len(s.items) == 1


def test_429_retried_at_most_twice(env):
    call, budget, sleeps, lines = env
    s = Session(*[Resp(429, {"error": {"code": 429, "message": "slow down"}}, {"Retry-After": "60"})] * 4)
    with pytest.raises(llm.LLMError, match="429"):
        call(s)
    assert budget.calls == 3 and sleeps == [20.0, 20.0]  # Retry-After capped at 20s
    evs = [e for e in lines() if e["action"] == "llm_call"]
    assert [e["attempt"] for e in evs] == [1, 2, 3] and all(e["result"] == "fail" for e in evs)


def test_503_then_success(env):
    call, budget, sleeps, lines = env
    s = Session(Resp(503, {"error": {"code": 503, "message": "no provider"}}), ok())
    content, _ = call(s)
    assert content == {"sum": 42} and budget.calls == 2 and sleeps == [2.0]
    evs = [e for e in lines() if e["action"] == "llm_call"]
    assert evs[1]["retry_reason"] == "http_503"


def test_timeout_retried_and_charged_worst_case(env):
    call, budget, _, _ = env
    s = Session(requests.Timeout(), ok())
    call(s, max_tokens=500)
    assert budget.calls == 2 and budget.completion_tokens == 500 + 20 and budget.estimated


def test_error_inside_http_200(env):
    call, budget, _, _ = env
    s = Session(Resp(200, {"error": {"code": 400, "message": "bad param"}}))
    with pytest.raises(llm.LLMError, match="400"):
        call(s)
    assert budget.calls == 1


def test_truncated_and_starved(env):
    call, _, _, _ = env
    with pytest.raises(llm.Truncated) as e:
        call(Session(ok('{"sum": 4', finish="length", usage={**OK_USAGE, "completion_tokens": 500})))
    assert not isinstance(e.value, llm.ReasoningStarved)
    starved = {**OK_USAGE, "completion_tokens": 500, "completion_tokens_details": {"reasoning_tokens": 480}}
    with pytest.raises(llm.ReasoningStarved):
        call(Session(ok("", finish="length", usage=starved)))


def test_bad_json(env):
    call, _, _, _ = env
    with pytest.raises(llm.BadJSON):
        call(Session(ok("not json at all")))


def test_budget_blocks_call(env):
    call, budget, _, _ = env
    budget.calls = budget.soft_calls
    s = Session(ok())
    with pytest.raises(llm.BudgetExhausted):
        call(s)
    assert s.bodies == []


def test_max_tokens_clamped(env):
    call, budget, _, _ = env
    budget.completion_tokens = budget.soft_completion - 300
    s = Session(ok())
    call(s, max_tokens=7000)
    assert s.bodies[0]["max_tokens"] == 300


def test_missing_key(env, monkeypatch):
    call, budget, _, _ = env
    monkeypatch.delenv("OPENROUTER_API_KEY")
    with pytest.raises(llm.MissingKey):
        call(Session(ok()))
    assert budget.calls == 0


def test_trace_has_no_key_or_prompt(env):
    call, _, _, lines = env
    s = Session(Resp(429, {"error": {"code": 429, "message": f"echo {FAKE_KEY}"}}), ok())
    call(s)
    raw = json.dumps(lines())
    assert "sk-or-" not in raw and "secret prompt text" not in raw
    assert s.headers[0]["Authorization"].endswith("testkey")  # the key is sent, just never logged
