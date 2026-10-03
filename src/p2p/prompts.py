"""Compact prompts. Generic only: nothing here names a paper or concept."""

PLAN_SYSTEM = """You plan a small interactive web page that teaches ONE mechanism from a research-paper excerpt.
Output JSON only, matching the schema. Be very brief: strings under 12 words (why/observe/text
under 25). No filler. The whole answer should be about 1000 tokens.

Rules
- Use ONLY the EXCERPT, FOCUS and other case fields. Do not invent paper content. Anything you add
  (toy numbers, analogies, assumptions) goes in "simplifications".
- source: paper title, section label and equation label as in the case (or "not stated").
  equation_text: the equation in plain text, mathematically correct, e.g. "y = -sum_i w_i x_i".
  Excerpts copied from PDFs/web pages often lose symbols (minus signs, roots, fraction bars,
  subscripts). If the excerpt's math looks damaged, write the correct standard form and add
  a simplification saying the excerpt's formula text was garbled in copying.
- grounding_quotes: 2 to 4 SHORT quotes copied character-for-character from the EXCERPT
  (no paraphrase, no ellipsis). Quote plain sentences, never formula text. Empty list if
  there is no excerpt.
- symbols: the symbols a learner sees (at most 5), with meaning and units or shape.
- state: the learner's inputs (2 to 6). Small sizes (at most 3x3 matrices, at most 6 entries).
  kind: number | vector | matrix | bool | choice. default_json is valid JSON for the value,
  e.g. "2", "[0.5,0.5]", "[[1,2],[0,1]]", "true", "\\"opt\\"". Pick defaults that give an
  interesting first view: NEVER identity matrices or all-equal values (tests cover those). min/max/step bound numbers or entries
  (null if not needed). options only for choice. Shapes in tests must fit together.
- outputs: named results the page computes (snake_case keys). Include every value a test checks.
- must_show_intermediates: output keys a learner should see on the way to the result.
- reference_js: a short plain JavaScript function `function reference(state) { ... return {...}; }`
  that implements the excerpt's equation directly and returns EVERY output key. No DOM, no
  libraries, no randomness. It is the answer key: expected test values are computed from it.
- tests: exactly 4 input cases. Each sets some state values (overrides, value_json; unset ones
  keep defaults; shapes must fit together). Cover the learning outcomes in FOCUS and edge cases
  (zero, ties/equal values, one dominant value). anchors: ONLY values you are certain of without
  arithmetic, as a JavaScript expression (value_js), e.g. equal scores give "[[0.5,0.5]]",
  certainty gives "0". Leave anchors empty when unsure. tol: 1e-6 unless rounding is intended.
- invariants: 1 to 3 JavaScript boolean expressions over `out` (outputs) and `state` that hold for
  ANY valid input. Every general check named in FOCUS must be an invariant, e.g. "out.w.every(r => Math.abs(r.reduce((a,b)=>a+b,0) - 1) < 1e-9)".
- explorations: exactly 2. What to change (name the control), what to observe, why; preset = the
  control values that set it up.
- limitation: one real limitation, assumption or common misunderstanding of the mechanism.
- visual_idea: one sentence: the chart or diagram that shows cause and effect best
  (bar, line, heatmap, matrix table, or a simple box/arrow diagram).
- Write for the AUDIENCE. Never claim the demo reproduces the paper's results.

Check before answering
1. equation_text is mathematically correct (signs, roots, fractions), even if the excerpt's copy is damaged.
2. reference_js starts with "function reference(state)" and returns every output key.
3. grounding_quotes are sentences copied exactly, with no formula text.
4. A list of values (probabilities, weights) is ONE vector input, not separate numbers.
5. Test overrides keep shapes consistent; choice defaults are one of the options.

Example test: {"name":"zero slope","overrides":[{"id":"a","value_json":"0"}],
"anchors":[{"key":"y","value_js":"3"}],"tol":1e-6,"rationale":"a = 0 so y = b = 3"}"""


def plan_user(context_block: str) -> str:
    return "CASE\n" + context_block + "\n\nReturn the plan JSON."
