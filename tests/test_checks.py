import json
from pathlib import Path

from p2p.assemble import assemble
from p2p.build import checks_wrapper, make_spec
from p2p.case import load_case
from p2p.checks import run_checks, score

FX = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).resolve().parents[1]
PLAN = json.loads((FX / "plan_attention.json").read_text(encoding="utf-8"))
BUILD = json.loads((FX / "build_attention.json").read_text(encoding="utf-8"))
CASE = load_case(ROOT / "cases" / "a_attention.json")


def check(build=None, plan=None, html_edit=None):
    b, p = build or BUILD, plan or PLAN
    spec = make_spec(CASE, p, b)
    built = {"spec": spec, "compute_js": b["compute_js"], "page_js": checks_wrapper(b["compute_js"], p["invariants"])}
    html = assemble(spec, built["page_js"])
    if html_edit:
        html = html_edit(html)
    return {r.name: r for r in run_checks(CASE, p, built, html)}


def failed(res):
    return sorted(n for n, r in res.items() if r.passed is False)


def test_good_build_passes_everything():
    res = check()
    assert failed(res) == []
    assert score(list(res.values())) == (0, 0, 0)


def test_compute_that_ignores_the_scaling_toggle():
    js = BUILD["compute_js"].replace("var s = state.scale ? 1 / Math.sqrt(dk) : 1;", "var s = 1 / Math.sqrt(dk);")
    assert js != BUILD["compute_js"]
    res = check(dict(BUILD, compute_js=js))
    f = failed(res)
    assert "controls_have_effect" in f and "scale" in res["controls_have_effect"].detail
    assert any(n.startswith("test:") for n in f)  # the 'scaling off' test now disagrees
    bad = next(r for n, r in res.items() if n.startswith("test:") and r.passed is False)
    assert "expected" in bad.detail and "got" in bad.detail and bad.target == "compute_js"


def test_wrong_formula_is_caught_on_random_inputs():
    js = BUILD["compute_js"].replace("return t * s;", "return t * s * 1.01;")
    res = check(dict(BUILD, compute_js=js))
    assert "compute_matches_reference_on_variations" in failed(res)


def test_external_script_and_url_are_critical():
    res = check(html_edit=lambda h: h.replace("</head>", '<script src="https://cdn.example.com/x.js"></script></head>'))
    assert {"offline_no_urls", "offline_no_network_code"} <= set(failed(res))
    assert res["offline_no_urls"].severity == "critical"


def test_fetch_in_page_is_caught():
    res = check(html_edit=lambda h: h.replace("</body>", "<script>fetch('data.json')</script></body>"))
    assert "offline_no_network_code" in failed(res)


def test_invented_quote_is_caught():
    plan = dict(PLAN, grounding_quotes=PLAN["grounding_quotes"] + ["Attention is a kind of magic."])
    res = check(plan=plan)
    assert "quotes_in_excerpt" in failed(res)


def test_compute_that_throws_is_critical():
    res = check(dict(BUILD, compute_js="function compute(state) { throw new Error('boom'); }"))
    assert failed(res) == ["compute_runs_on_defaults"] and res["compute_runs_on_defaults"].severity == "critical"


def test_nan_and_missing_outputs():
    js = BUILD["compute_js"].replace("output: output,", "output: [[NaN]],").replace("scores: scores,", "")
    res = check(dict(BUILD, compute_js=js))
    assert {"outputs_present", "outputs_finite_on_defaults"} <= set(failed(res))


def test_visual_pointing_at_missing_data():
    vis = BUILD["visuals"][:2] + [{"kind": "bar", "title": "x {outputs.nothing:2}", "labels": "klabels", "data": "nope"}]
    res = check(dict(BUILD, visuals=vis))
    assert "visual_data_refs" in failed(res)
    assert "nope" in json.dumps(res["visual_data_refs"].detail)


def test_missing_exploration_text_is_critical():
    plan = json.loads(json.dumps(PLAN))
    plan["explorations"][1]["why"] = ""
    res = check(plan=plan)
    assert "required_sections" in failed(res) and "explore-2" in res["required_sections"].detail


def test_infinite_loop_does_not_hang():
    res = check(dict(BUILD, compute_js="function compute(state) { while (true) {} }"))
    assert failed(res) == ["compute_runs_on_defaults"]


def test_quote_with_angle_brackets_is_found():
    from p2p.case import Case
    from p2p.checks import static_checks
    case = Case("u", "f", "a", extra={"excerpt": "it converges when 0 < eta < 2/a, and for eta > 2/a they diverge."})
    spec = {"grounding": {"from_excerpt": ["for eta &gt; 2/a they diverge"]}}
    res = {r.name: r for r in static_checks(case, spec, "")}
    assert res["quotes_in_excerpt"].passed is True


def test_random_inputs_respect_integer_steps():
    from p2p.checks import perturbations
    plan = {"state": [{"id": "n", "kind": "number", "default": 5, "min": 1, "max": 20, "step": 1}]}
    for _, st in perturbations(plan, set()):
        assert float(st["n"]).is_integer()
