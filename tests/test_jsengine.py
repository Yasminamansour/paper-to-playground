import pytest

from p2p.jsengine import JSError, compiles, eval_js


def test_eval_numbers_and_arrays():
    assert eval_js("[1/2, [Math.log2(4)]]") == [0.5, [2]]


def test_no_network_or_host_apis():
    assert eval_js("[typeof fetch, typeof require, typeof XMLHttpRequest, typeof process]") == ["undefined"] * 4


def test_infinite_loop_is_stopped():
    with pytest.raises(JSError):
        eval_js("(() => { while (true) {} })()", time_limit=0.2)


def test_syntax_error_and_compiles():
    with pytest.raises(JSError):
        eval_js("[1, 2")
    assert compiles("function(out){ return out.a > 0; }") is None
    assert compiles("function(out){ return out.a > ; }")
