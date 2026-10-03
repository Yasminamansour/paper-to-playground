import json
import re
from pathlib import Path

from p2p.assemble import assemble, embed_json, sanitize_html, sanitize_spec, write_page

FX = json.loads((Path(__file__).parent / "fixtures" / "generic_spec.json").read_text(encoding="utf-8"))


def test_sanitizer_keeps_text_and_math():
    s = 'a<sub>i</sub> <b>bold</b> <math display="block"><mfrac><mn>1</mn><mi>n</mi></mfrac></math>'
    assert sanitize_html(s) == s


def test_sanitizer_strips_dangerous_things():
    bad = ('<script>alert(1)</script><img src=x onerror=alert(1)><a href="javascript:x">link</a>'
           '<b onclick="x()" style="color:red">ok</b><svg><script>1</script></svg><iframe src="x"></iframe>')
    out = sanitize_html(bad)
    assert out == "link<b>ok</b>"
    for word in ("script", "onerror", "onclick", "href", "src", "style", "iframe", "svg"):
        assert word not in out


def test_sanitizer_escapes_text_and_closes_tags():
    assert sanitize_html("x < y & z") == "x &lt; y &amp; z"
    assert sanitize_html("<b><i>open") == "<b><i>open</i></b>"


def test_spec_nan_becomes_null_and_json_is_script_safe():
    clean = sanitize_spec({"v": float("nan"), "t": "a</script><script>alert(1)</script>"})
    assert clean["v"] is None
    js = embed_json({"t": "</script><!--"})
    assert "</" not in js and "<!--" not in js


def test_page_is_single_offline_file(tmp_path):
    path = write_page(tmp_path, FX["spec"], FX["compute_js"])
    page = path.read_text(encoding="utf-8")
    assert page.count("{{") == 0
    assert not re.search(r"https?://|@import|<script[^>]+src=|<link[^>]+href=|url\((?!#)", page)  # url(#id) = local SVG marker, fine
    assert len(page.encode("utf-8")) < 150_000
    assert page.lower().count("</script>") == 4  # json + error hook + compute + runtime


def test_compute_cannot_break_out_of_script():
    page = assemble(FX["spec"], "function compute(s){ return {outputs:{t:'</script><b>x'}}; }")
    assert "</script><b>x" not in page


def test_placeholder_text_in_content_is_not_expanded():
    spec = dict(FX["spec"], subtitle="literal {{RUNTIME_JS}} text")
    page = assemble(spec, FX["compute_js"])
    assert "literal {{RUNTIME_JS}} text" in page
