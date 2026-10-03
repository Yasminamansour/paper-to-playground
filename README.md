# Paper to Playground

An agent that turns a focused research-paper excerpt into one offline, interactive HTML page that teaches the
mechanism to an engineering undergraduate. EECE503P / EECE798S Agentic Systems hackathon.

MODEL_ID: deepseek/deepseek-v4.1-flash

## Team

| Name | ID |
|---|---|
| Yasmina Mansour | 202403028 |
| Zenab Bassam | 202401872 |
| Mustafa Abbara | 202772707 |

## Run it

Python 3.11. No GPU, system packages or browser downloads.

```
python -m pip install -r requirements.txt
export OPENROUTER_API_KEY=...            # Windows PowerShell: $env:OPENROUTER_API_KEY = "..."
python agent.py --input case.json --output out --model deepseek/deepseek-v4.1-flash
```

Outputs: `out/index.html` (one self-contained file, works offline) and `out/trace.jsonl`.
Exit code: `0` = page written with no critical check failures, `1` = generation failed (a page is still written
whenever one exists), `2` = bad input or usage. The run also leaves `plan.json` and `build.json` in the output
folder for inspection.

## How it works

```
case.json ──► PLAN call ──► answer key ──► BUILD call ──► assemble ──► CHECKS ──► (REPAIR ≤ 2) ──► best page
              (1 call)     (QuickJS,       (1 call)       (template)   (QuickJS,     only failing     index.html
                            no tokens)                                  no tokens)    parts            trace.jsonl
```

1. **Plan** (one call, strict JSON schema). The model reads the excerpt and focus and returns the concept, source
   labels, short verbatim quotes, symbols, the learner's inputs, outputs, 4 test inputs, invariants, 2 explorations,
   a limitation, and `reference_js`: the paper's equation written as a small JavaScript function.
2. **Answer key, no tokens.** Python runs `reference_js` in the QuickJS engine on every test input to get the
   expected values. The model's own hand-computed values are kept only if they agree with the reference.
   Quotes not found word-for-word in the excerpt are dropped; invariants that fail on the reference are dropped.
   We do this because the model wrote correct formulas but often got matrix arithmetic wrong in its head.
3. **Build** (one call, JSON). The model writes only what needs judgment: title, explanation, control labels and
   layout, 1–3 visuals and `compute(state)`. Python fills everything the plan already fixed (symbols, controls,
   explorations, limitation, source section) and turns the invariants into live self-checks shown on the page.
4. **Assemble.** A generic template (`src/p2p/template/`) renders any spec: sliders, number fields, toggles,
   selects, editable matrices and vectors, probability vectors with Normalize; bar, line, heatmap, matrix-table and
   simple SVG diagrams; intermediate values; self-checks. All text from the model is sanitized (a few text tags
   plus MathML, no attributes, no URLs). The page has no CDN, fonts, images or network code.
5. **Checks, no tokens** (`src/p2p/checks.py`): offline scan, size, the 7 required sections, quotes in the excerpt,
   `compute()` runs and returns finite planned outputs, every test matches the answer key (expected vs got in the
   trace), `compute()` agrees with the reference on generic input variations (min, max, zeros, ties, seeded
   random), invariants hold, no crash on valid inputs, every control changes the result, every chart points at
   existing data.
6. **Repair.** Only if a critical or major check fails: free fixes in Python first, then at most 2 calls that receive
   only the failing checks and the fields to change, and return a JSON patch. Every version is scored
   (critical, major, minor failures) and the best one is written.
7. **Fallbacks.** If the build call fails, a plain page that runs `reference_js` is assembled (it passes the same
   checks). A watchdog writes the best page and the trace summary at 570 s.

There is no paper-specific code, lookup table or prewritten page anywhere. The template and checks are generic;
everything concept-specific comes from the two model calls.

## Limits and safeguards

| Limit (per case) | Rule | Our guard |
|---|---|---|
| ≤ 10 requests incl. retries | every HTTP attempt is counted | soft cap 8; retries only on 408/429/502/503/timeout, max 2 |
| ≤ 30,000 completion tokens | from OpenRouter `usage` | soft cap 26,000; `max_tokens` clamped to what is left; reasoning off |
| ≤ 10 minutes | process start to exit | per-request timeout from time left; watchdog at 570 s |
| network = OpenRouter only | never fetch `source_url` | only `requests.post` to the chat completions URL |

Model settings (`src/p2p/config.py`, keyed by MODEL_ID only): reasoning disabled, temperature 0, strict JSON schema
for the plan, providers ordered `Together` first (it counted ~1,200 fewer prompt tokens than another provider for
the same request), fallbacks allowed. The key is read from `OPENROUTER_API_KEY` and is never printed, logged,
committed or embedded in the page.

## Input

UTF-8 JSON. Required strings: `source_url`, `focus`, `audience`. Every other field is kept and passed to the model
as labelled context, `excerpt` first (e.g. `title`, `section`, `excerpt`, `notes`). Non-string fields are
JSON-encoded; a field longer than 12,000 characters is cut and the cut is logged. A missing excerpt is allowed:
the quote check is then marked `skip`, never `pass`.

## Trace (`out/trace.jsonl`)

One JSON object per line, written as it happens. Every event has `ts`, `t` (seconds since start), `stage`,
`action`, `result` (`ok`/`fail`/`skip`/`info`).

| action | extra fields |
|---|---|
| `llm_call` | `call_index`, `purpose`, `model`, `provider`, `prompt_tokens`, `completion_tokens`, `reasoning_tokens`, `cached_tokens`, `total_tokens`, `elapsed_s`, `finish_reason`, `generation_id`, `http_status`, `attempt`, `retry_reason`, `max_tokens`, `prompt_fp` (length + sha256 prefix only) |
| `check:<name>` | `detail` (e.g. expected vs got), `severity`, `target` |
| `revision` | `round`, `targets`, `reason`, `before`/`after` failure counts |
| `write_page` | chosen `version`, size, remaining failures |
| `finish` (summary) | `exit_code`, `elapsed_s`, `calls`, prompt/completion/reasoning/cached/total tokens, checks passed/failed, revisions |

No message text, model reasoning or credentials are ever logged.

## Results on our practice cases

`tools/harness.py` runs every case in `cases/` twice in fresh folders (8 cases, 16 runs; saved in
`docs/harness_summary.json`): 16/16 exit 0, 16/16 pass every check, median 6,270 total tokens and 11.9 s, 2 calls
per run (one run needed one repair call). Cases `c_`–`h_` use excerpts we paraphrased for testing (see
`cases/README.md`), including one with no excerpt and one over the length limit.

## Example

`examples/attention/` holds `case.json`, `index.html` and `trace.jsonl` exactly as generated by one harness run of
the public Example A (2 calls, 6,717 tokens, 13.5 s, 25/25 checks). Assessed outputs are generated afresh.

## Repository

```
agent.py                 CLI and orchestration (plan → build → checks → repair → best page)
src/p2p/                 case loader, trace, budget, OpenRouter client, config, prompts, schemas,
                         plan, build, checks, repair, assemble, jsengine (QuickJS sandbox)
src/p2p/template/        page.html, style.css, runtime.js (generic, offline)
tests/                   pytest suite (83 tests, no network)
tools/                   harness.py, report.py, offline_scan.py, ping.py, set_excerpt.py,
                         render_fixture.py, smoke_browser.py (dev only), scan_secrets.py + install_hooks.py
cases/                   practice inputs        examples/   one generated input/output pair
docs/                    assignment text, build roadmap, harness results
```

## Known limitations

- The page explains one mechanism with small inputs. Ideas that need large data, training or animation over time
  are reduced to a small worked case.
- The answer key is only as good as `reference_js`; anchors and invariants catch many errors but not all.
- Excerpts copied from PDFs or web pages can lose math symbols; the model is told to restore the standard form and
  say so under "our simplifications".
- Charts are limited to the template's types (bar, line, heatmap, matrix table, simple SVG).

## Credits and reuse

- Libraries: [requests](https://pypi.org/project/requests/) (Apache-2.0) for HTTP;
  [quickjs-ng](https://pypi.org/project/quickjs-ng/) (MIT) to run generated JavaScript in a sandbox for checks.
  Dev only: pytest (MIT), Playwright (Apache-2.0) for local browser smoke tests.
- Chart colors follow a colorblind-checked categorical/sequential palette from Anthropic's data-visualization
  guidance used by Claude.
- AI assistance: the code, prompts and tests were written with Claude (Anthropic) as a coding assistant, stage by
  stage, and reviewed and run by the team. The build plan in `docs/ROADMAP.md` was also prepared with AI help.
- All code was written for this project; no other code was copied.
