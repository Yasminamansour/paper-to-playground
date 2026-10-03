# Practice cases

- `a_attention.json`, `b_entropy.json`: the two public examples from the assignment; excerpts copied from the papers.
- `c_` to `f_`: self-made cases of comparable scope on other papers. **Their excerpts are short paraphrases written by
  the team for testing, not copied paper text.** Replace them with the real section text (`python tools/set_excerpt.py
  cases/<file>.json` after copying the section) to test closer to assessment conditions.
- `g_awkward_no_excerpt.json`: no excerpt, no section, extra fields of other types (number, list, object, null).
- `h_long_excerpt.json`: an excerpt over the 12,000-character limit (tests truncation), with the relevant part in the middle.

None of these files is read by the agent code; they are inputs for `tools/harness.py` only.
