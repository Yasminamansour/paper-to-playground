# Paper to Playground: A Staged Build Roadmap You Can Hand to Claude, One Stage at a Time

Build it as a template-first, budget-guarded pipeline. Python owns a pre-built, generic, offline HTML/JS "playground" template. The model makes a small number of compact calls, typically 2 and at most 4: a short **plan** call that extracts the equation, symbols, section and *test cases with expected values*, then a **build** call that returns a JSON spec plus a pure-JS `compute()` function. Python checks the result deterministically, running the JS in an embedded engine and comparing against the planned tests. Only failing parts get a targeted **repair** call. Each of the 10 stages below (0–9) comes with a ready-to-paste Claude prompt, verification commands with pass criteria, a git checkpoint and a time budget. Together they fill exactly 360 minutes, including a 10-minute buffer.

## TL;DR

- **Architecture:** generic template + plan → build → deterministic checks → targeted repair, run under a hard budget guard. The guard enforces ≤10 requests including retries, ≤30k completion tokens and ≤10 minutes, and normally uses 2 calls and well under 15k total tokens. The LLM never writes the whole HTML page. It writes content plus a small `compute()` module, which keeps tokens low and stops the JS from breaking.
- **Pin everything that can blow the budget.** Pick a MODEL_ID whose reasoning can be capped (OpenRouter reasoning tokens count against `max_tokens` and are billed as output). Take usage straight from the response's `usage` object, which OpenRouter now always includes. Never fetch arXiv at assessment time; rely on the excerpt in `case.json`. Verify numbers by executing the JS in a pip-only engine such as `quickjs-ng` or `mini-racer`, not Playwright, which needs a separate browser download.
- **Workflow:** one commit and one annotated tag per stage, with secrets kept out of git through `.gitignore` plus a pre-commit scan. The test harness runs the two public cases plus 3–5 self-made cases. Freeze about 25 minutes before the end, record `git rev-parse HEAD`, check it against `git ls-remote`, and submit the 40-character SHA.

## Key Findings

**OpenRouter facts that shape the design**
- **Usage is always returned.** OpenRouter's docs say every response includes prompt and completion token counts from the model's native tokenizer, plus reasoning and cached token counts where applicable, "No additional parameters are required".\[1\] They also say `usage: { include: true }` is "deprecated and [has] no effect".\[1\] Read `usage.prompt_tokens`, `usage.completion_tokens`, `usage.completion_tokens_details.reasoning_tokens` and `usage.prompt_tokens_details.cached_tokens`. Log the response `id`; it is a generation id that can later be checked against `GET /api/v1/generation?id=…`, which returns native token counts including reasoning and cached tokens.\[1\]\[2\]\[3\]
- **Reasoning tokens are the main budget risk.** OpenRouter's docs say reasoning tokens "are considered output tokens and charged accordingly" and on most providers count against `max_tokens`. If the budget runs out mid-reasoning, the response returns `finish_reason: "length"` with empty `content`, and the reasoning tokens are still billed. You can control this with `reasoning: {effort: "none" | "minimal" | "low" | …}` or `reasoning: {max_tokens: N}`. `exclude: true` only hides reasoning from the response; the tokens are still billed. Models flagged `mandatory` reject `effort: "none"`. To detect the problem, compute `completion_tokens − reasoning_tokens`; a value near 0 means reasoning consumed the budget.\[4\]
- **Structured outputs are available but not guaranteed.** OpenRouter supports `response_format: {type: "json_schema", json_schema: {..., strict: true}}`.\[5\] Its docs warn that "exact compliance is not guaranteed on every endpoint", so set `provider: {require_parameters: true}`.\[5\]\[6\] The opt-in `response-healing` plugin repairs JSON *syntax*, but it cannot fix output truncated by `max_tokens` and does not enforce your schema.\[6\]\[7\]\[8\] Always validate on your side.
- **Errors and retries.** The error codes are 400 (bad params), 401 (bad key), 402 (no credits), 403 (moderation/guardrail), 408 (timeout), 429 (rate limit), 502 (model down / invalid response) and 503 (no provider meets routing requirements). OpenRouter may send `Retry-After` on 429 and 503, and it warns that errors can arrive inside an HTTP 200 body, so check for an `error` field every time.\[9\] Only retry 408, 429, 502 and 503, and every retry counts toward your 10 requests.
- **Do not use response caching.** OpenRouter's response cache reports *all usage counters as 0* on a hit.\[10\] That is at best "unverifiable usage", and the rubric scores missing or unverifiable usage as 0 token-efficiency points. It could also look like reward hacking.

**Offline verification in Python (pip only)**
- **Playwright is out for `requirements.txt`.** `pip install playwright` installs only the library; the browsers need a separate `playwright install` download, which counts as manual setup.\[11\]\[12\] Keep Playwright as an optional *dev-only* smoke test.
- **`quickjs-ng` is the best primary JS engine.** Its 0.16.2.1 PyPI page lists small wheels (e.g. 522.1 kB for a Windows AMD64 wheel), and its README says "Pre-built wheels are available for Linux (x86_64, i686, aarch64), Windows (AMD64, x86), and macOS (arm64), for CPython 3.10+ and PyPy 3.10/3.11." Its API is `Context().eval`, `ctx.get("fn")`, `set_time_limit(seconds)` and `set_memory_limit(bytes)`, and it works as a drop-in replacement for the archived `quickjs` package. One caveat: there is no macOS Intel wheel, and one Context is not thread-safe.
- **`mini-racer` (V8) is the fallback.** Version 0.14.1 (released Feb 1, 2026) is, per its PyPI page, "compatible with Python 3.10-3.14", with `py3-none` wheels (~15–22 MB) for manylinux_2_27, musllinux_1_2, macOS and Windows (x64 and ARM64). Usage looks like `ctx.eval(code, timeout_sec=2)` and `fn = ctx.eval("a => a*a"); fn(4)`. Linux needs glibc ≥ 2.27, and Alpine additionally needs `gcompat`.
- **Avoid `dukpy` and `esprima`.** `dukpy` runs ES5 only, so modern arrow functions and `const` break. `esprima` (Python port) parses only up to ES2017.\[13\] Neither is needed if an engine compiles the code.

**Page technology**
- **Use native MathML for equations.** Google's "New in Chrome 109" post says "MathML Core is now supported in Chrome," so Chromium can show proper equations without MathJax or KaTeX. Unicode plus `<sub>`/`<sup>` is a safe fallback.
- **The template should own all DOM code**, using inline SVG/Canvas helpers. The LLM supplies only declarative specs and a pure `compute(state)` function. That removes the largest source of broken JS.

## Recommended Architecture

Text diagram (put this in the README):

```
case.json ──► load_case() ──► [Budget Guard: calls≤8 (hard cap 10), completion≤26k (hard cap 30k), deadline 540s]
                │
                ▼
     (1) PLAN call (≈600–1,200 completion tokens, JSON schema)
         → concept, equation (as written in excerpt), section/eq label,
           symbols+meanings, controls to expose, intermediate values,
           ≥4 numeric TEST CASES with expected values + invariants,
           2 explorations, 1 limitation, grounding quotes (verbatim from excerpt)
                │
                ▼
     (2) BUILD call (≈3,000–6,000 completion tokens, JSON schema)
         → content blocks (HTML-lite), controls spec, outputs spec,
           visual spec (bar|line|heatmap|matrix|svg-primitives),
           compute.js: pure function compute(state) → {values, intermediates}
                │
                ▼
     (3) DETERMINISTIC CHECKS (Python, no tokens)
         static: offline-only, required sections, ≥2 controls bound, labels,
                 grounding quotes ⊂ excerpt, size limits
         numeric: run compute.js in QuickJS on defaults, edge inputs, and
                  PLAN test cases; invariants (finite, sums, ranges)
                │ all pass? ──yes──► assemble template → out/index.html, exit 0
                │ no
                ▼
     (4) REPAIR call(s) (≤2 rounds, ≈1,000–3,000 tokens each): send ONLY failing
         checks + failing field(s); receive JSON patch; re-check
                │
                ▼
     best passing (or least-failing) version → index.html; trace.jsonl summary; exit code
```

Why it fits the rubric:
- **Scientific accuracy (25):** the plan pins the equation and expected values *before* any code exists. The checks then compare an independent expectation against executed code, so neither one is simply trusted.
- **Working interaction (15):** controls come from a tested generic library, not freshly written JS.
- **Autonomous checks (10):** every check and revision is a real trace event.
- **Token efficiency (10) and latency (5):** the long HTML and CSS never pass through the model.

The trade-off: quality is worth 85 points, and you must reach ≥50 before efficiency points count. So never skip the plan call to save tokens. Cut prompt verbosity instead, and skip repair when the checks pass.

## Assumptions

- The team builds within the six-hour window. You may prepare **generic** scaffolding in advance only if the course rules allow it, and the spec permits "helper files/generic templates" but not "paper-specific prewritten answers or generated pages". **Confirm with Ammar Mohanna before preparing anything ahead of time.**
- **Allowed** generic scaffolding: the CLI, OpenRouter client, trace logger, budget guard, the HTML template and design system, generic widgets (slider, number field, editable matrix, toggle, select, bar/line chart, heatmap, value table, SVG arrow/box primitives), the checker, the harness and the practice `case.json` files.
- **Forbidden:** concept-specific widgets or code paths (`if "attention" in focus:`), stored pages or answers for any paper, lookup tables keyed on URLs, and full example pages pasted into prompts as few-shot answers.
- `case.json` may contain fields beyond `source_url`, `focus` and `audience`. The spec says "five required string fields" but names only three, and it says each hidden case "contains an excerpt", so assume something like `excerpt`, plus maybe `title` or `section`. The loader must require only the three named fields and pass every other string field to the model as labelled context.
- During assessment, network access is limited to OpenRouter. The agent must never depend on fetching `source_url`.
- You choose the MODEL_ID and record it in the README. Test it yourself for reasoning control, JSON-schema support, speed and token use.

## The Stages

> **Tip before Stage 1:** In Stage 0, create `CLAUDE.md` (Claude Code reads it automatically) and `docs/SPEC.md` holding the full assignment text. Every stage prompt below begins with "Read CLAUDE.md and docs/SPEC.md first", so each prompt is self-contained without repeating the spec.

### Stage 0 — Repo, environment, secret protection (15 min)

**Goal:** an empty but correctly structured repository that cannot leak the API key.

**Prompt for Claude:**
```
Create the initial scaffold for a Python 3.11 project "paper-to-playground".
Files:
- agent.py (stub: argparse with --input, --output, --model; prints "not implemented"; exit 2)
- requirements.txt (pin exact versions: requests==<current>, quickjs-ng==<current>; nothing else yet)
- requirements-dev.txt (pytest, optional playwright for local smoke tests ONLY)
- .gitignore: .env, .venv/, __pycache__/, out*/, runs/, *.log, .DS_Store
- .env.example with OPENROUTER_API_KEY= (empty) 
- CLAUDE.md: project rules: Python 3.11; agent.py at root; all LLM calls via OpenRouter
  https://openrouter.ai/api/v1/chat/completions with Bearer key from env OPENROUTER_API_KEY;
  never print/log/commit the key; per-case limits: ≤10 API requests incl. retries,
  ≤30,000 completion tokens, ≤10 minutes; network at assessment = OpenRouter only
  (never fetch source_url); output out/index.html (single self-contained file, no CDN,
  no remote fonts/images, no external URLs) + out/trace.jsonl; no paper-specific code
  or prewritten pages; generic templates only.
- docs/SPEC.md: (I will paste the assignment text here)
- src/p2p/__init__.py, tests/__init__.py, cases/ (empty), tools/ (empty)
Also write tools/scan_secrets.py: scans staged files (git diff --cached --name-only) for
patterns like 'sk-or-v1-' and 'OPENROUTER_API_KEY=' followed by a non-empty value; exit 1
if found. Install it as .git/hooks/pre-commit via tools/install_hooks.py.
```

**Verify:**
```
python3.11 -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements.txt && python -m pip install -r requirements-dev.txt
python tools/install_hooks.py
echo "OPENROUTER_API_KEY=sk-or-v1-FAKE" > leak.txt && git add leak.txt && git commit -m test   # must be BLOCKED
git restore --staged leak.txt && rm leak.txt
python agent.py --input x --output y --model z; echo $?   # prints stub, exit 2
```
Pass: the install succeeds on a clean venv, the hook blocks the fake key, and `git status` shows no `.env`.

**Git checkpoint:** create the repo on GitHub (public, or private with the instructor invited; see the Caveats section). Then:
`git add -A && git commit -m "chore: scaffold, pinned deps, secret guard, project rules" && git push -u origin main && git tag -a v0.0-scaffold -m "Stage 0" && git push --tags`.
Also turn on GitHub secret scanning / push protection if your repo settings allow it.\[14\]\[15\] OpenRouter's authentication docs state that "OpenRouter is a GitHub secret scanning partner, and has other methods to detect exposed keys."

### Stage 1 — CLI, case loader, trace logger, budget guard (25 min)

**Goal:** the exact interface, robust input handling and an honest trace before any LLM code exists.

**Prompt for Claude:**
```
Read CLAUDE.md and docs/SPEC.md first. Implement:
1) src/p2p/case.py: load_case(path) -> Case. UTF-8 JSON. Required string fields:
   source_url, focus, audience (fail with clear error + nonzero exit if missing/non-string).
   Keep ALL other string fields (e.g. excerpt, title, section, paper_title, notes) in
   case.extra (ordered dict); never crash on unknown fields; non-string extras are
   json-dumped. Provide case.context_block(): a compact labelled text block
   ("EXCERPT:\n...", "TITLE: ...") with excerpt first; truncate any single field > 12,000
   chars with a marker, and record truncation in the trace.
2) src/p2p/trace.py: Trace(out_dir) writing out/trace.jsonl, one JSON object per line,
   flushed immediately. Every event: {"ts": ISO8601, "t": seconds since process start,
   "stage": str, "action": str, "result": "ok"|"fail"|"skip"|"info", ...extra}.
   Helpers: trace.llm_call(...) (fields: call_index, purpose, model, prompt_tokens,
   completion_tokens, reasoning_tokens, cached_tokens, total_tokens, elapsed_s,
   finish_reason, generation_id, http_status, attempt, retry_reason),
   trace.check(name, passed, detail), trace.revision(round, targets, reason),
   trace.summary(totals). A redact() filter must drop any value containing 'sk-or-'
   and any key named authorization/api_key; never log message contents or any
   'reasoning' field—log only prompt char length and a sha256 prefix.
3) src/p2p/budget.py: Budget(max_calls=10, max_completion=30000, deadline_s=600) with
   safety margins: soft caps calls=8, completion=26000, deadline=540s.
   can_call(requested_max_tokens) -> bool; clamp_max_tokens(requested) returns
   min(requested, remaining_completion_soft); record(usage). Count EVERY HTTP attempt
   (including retries and failed ones) as a call.
4) agent.py: parse args, create out dir, start Trace, load case, write a placeholder
   index.html ONLY in --dry-run mode, emit summary, exit codes: 0 success, 1 generation
   failed, 2 bad input/usage.
Add pytest tests for: missing field, extra fields preserved, redaction, budget clamp,
trace lines are valid JSON.
```

**Verify:**
```
python -m pytest -q
echo '{"source_url":"u","focus":"f","audience":"a","excerpt":"e","weird":5}' > /tmp/c.json
python agent.py --input /tmp/c.json --output /tmp/o --model m --dry-run; echo $?
python -c "import json;[json.loads(l) for l in open('/tmp/o/trace.jsonl')];print('trace ok')"
grep -c "sk-or-" /tmp/o/trace.jsonl   # must print 0
```
Pass: all tests green, the dry run exits 0, a missing `focus` exits 2, and every trace line parses and contains `stage`, `action` and `result`.

**Git:** `git commit -am "feat: CLI, robust case loader, jsonl trace, budget guard" && git tag -a v0.1-skeleton -m "Stage 1" && git push --follow-tags`.

### Stage 2 — OpenRouter client with usage accounting and safe retries (30 min)

**Goal:** a single `chat()` function that cannot break the call, token or time limits.

**Prompt for Claude:**
```
Read CLAUDE.md. Implement src/p2p/llm.py using requests only.
chat(messages, *, model, max_tokens, schema=None, purpose, budget, trace) -> (text, usage)
- POST https://openrouter.ai/api/v1/chat/completions, headers Authorization: Bearer
  $OPENROUTER_API_KEY, Content-Type: application/json. Read key from env; if missing,
  trace a failure and exit 1 (never print the key).
- Body: model, messages, max_tokens=budget.clamp_max_tokens(max_tokens), temperature=0.2,
  stream=false. If schema: response_format={"type":"json_schema","json_schema":
  {"name":purpose,"strict":true,"schema":schema}}, provider={"require_parameters":true},
  plugins=[{"id":"response-healing"}].
- Reasoning control from config (src/p2p/config.py, keyed only by MODEL_ID string, NOT by
  paper): e.g. {"effort":"low","exclude":true} or {"max_tokens":N,"exclude":true} or None.
- Do NOT send usage:{include:true} (deprecated, no effect). Do NOT enable response caching.
- Parse usage: prompt_tokens, completion_tokens,
  completion_tokens_details.reasoning_tokens, prompt_tokens_details.cached_tokens,
  total_tokens; keep response "id" as generation_id. If usage missing, record
  usage_missing=true in trace (do not invent numbers).
- Errors: check JSON body for "error" even on HTTP 200. Retry ONLY 408/429/502/503 and
  network timeouts, max 2 retries, honoring Retry-After (cap 20s) else backoff 2s,5s;
  never retry 400/401/402/403. Each attempt calls budget.record + trace.llm_call.
  Per-request timeout = min(150s, time remaining before soft deadline - 15s).
- Detect reasoning starvation: finish_reason=="length" and
  (completion_tokens - reasoning_tokens) < 50 -> raise ReasoningStarved.
- Detect truncation: finish_reason=="length" -> raise Truncated (caller decides).
- Parse JSON content robustly: strip ``` fences; json.loads; on failure raise BadJSON.
Write tools/ping.py that sends one tiny schema-constrained request and prints usage
fields and finish_reason (use it to choose MODEL_ID). Unit-test retry logic with a mocked
requests session (no network in tests).
```

**Verify:**
```
export OPENROUTER_API_KEY=...   # in your shell only, never in files
python tools/ping.py --model "$MODEL_ID"     # shows prompt/completion/reasoning/cached tokens
python -m pytest -q tests/test_llm.py
```
Pass: `ping.py` prints non-zero `prompt_tokens`/`completion_tokens`. `reasoning_tokens` is small or zero with your chosen reasoning setting. The mocked tests confirm that 401 is not retried, 429 is retried at most 2 times, and every attempt shows up in the trace. **Decide MODEL_ID now:** try 2–3 candidates with `ping.py` and pick one that supports `response_format`/structured outputs, allows reasoning to be capped or disabled, and responds quickly.

**Git:** `git commit -am "feat: OpenRouter client with usage accounting, bounded retries, reasoning caps" && git tag -a v0.2-client && git push --follow-tags`.

*Parallelization:* in a 2–3 person team, one teammate does Stage 3 on a branch (`git switch -c stage-3-template`) while another does Stage 2.

### Stage 3 — Generic offline HTML template and widget library (50 min)

**Goal:** a polished, accessible, self-contained page that renders **any** spec. This is the largest token saver and the biggest protection against broken interaction.

**Prompt for Claude:**
```
Read CLAUDE.md. Build src/p2p/template/ containing page.html, style.css, runtime.js, and
src/p2p/assemble.py that inlines CSS/JS and the spec into ONE html file.
Page contract: the generated content arrives as a JSON object SPEC embedded in
<script type="application/json" id="spec"> (escape "</" as "<\/"), plus a pure JS
module string COMPUTE defining function compute(state) that returns
{outputs:{...}, intermediates:[{label, value, note}], checks:[{label, pass, detail}]}.
runtime.js (vanilla ES2017, no external libs, no network APIs) must:
- Render sections in fixed order with ids: #idea (what/why it matters), #symbols (table:
  symbol, meaning, units/shape), #playground (controls + visual + intermediate values),
  #explore-1, #explore-2 (each: change / observe / why), #limitation, #grounding
  (paper title, source_url as plain text, section/equation label, "From the excerpt"
  quotes vs "Our simplification/example" items, and a fixed disclaimer that the toy demo
  does not reproduce the paper's experimental results).
- Generic controls from SPEC.controls: slider, number, toggle, select, editable matrix
  (rows×cols numeric grid with optional row/col add/remove within min/max), probability
  vector editor (with "normalize" button and live sum display). Each has id, label,
  default, min/max/step, help text. Every change re-runs compute(state) and re-renders.
- Generic visuals from SPEC.visual (one or more): bar, line (multiple series, axes,
  ticks, labels), heatmap with numeric cell labels, matrix table, and "svg" built from
  primitives (box, arrow, text, circle) whose labels/values may bind to outputs via
  "{outputs.key}" placeholders. All inline SVG; readable axis labels; colorblind-safe palette.
- Intermediate-values panel showing formatted numbers (configurable decimals), and a
  live "self-checks" panel from compute().checks.
- Robustness: wrap compute in try/catch, show a visible error box instead of a blank page;
  guard NaN/Infinity display as "undefined (see note)"; reset-to-defaults button;
  keyboard-accessible inputs with <label for>; prefers-reduced-motion respected.
- Math: allow SPEC strings to contain a safe HTML-lite subset (b,i,em,strong,sub,sup,code,
  br, and MathML Core elements math,mi,mn,mo,mrow,msub,msup,mfrac,msqrt,mover,munder,
  mtext,mtable,mtr,mtd). Sanitize with an allowlist in assemble.py (Python) — strip all
  other tags/attributes, any URL, on* handlers.
- System font stack only; no @import, no url() to remote resources.
Create tests/fixtures/generic_spec.json (a NEUTRAL dummy concept like "y = a·x + b",
not from any paper) and tools/render_fixture.py that assembles it to /tmp/fixture.html.
```

**Verify:**
```
python tools/render_fixture.py && cd /tmp && python -m http.server 8000   # open http://localhost:8000/fixture.html in Chromium
grep -nE "https?://|@import|<script[^>]+src=|<link[^>]+href=" /tmp/fixture.html   # only source_url text allowed
```
Pass in Chromium with DevTools open and **Network set to Offline**: there are no console errors, every control updates the chart and the intermediate values, the error box appears if you deliberately break `compute`, the page works from the keyboard, and the file is a single HTML under ~150 KB. Optional dev-only check: a Playwright script in `tools/smoke_browser.py` that clicks every control and asserts no console errors. It must never be imported by `agent.py`.

**Git:** `git commit -am "feat: generic offline playground template, widgets, sanitizer" && git tag -a v0.3-template && git push --follow-tags`. Merge the branch if you used one.

### Stage 4 — Spec schemas and the PLAN call (25 min)

**Goal:** a short, grounded plan that sets the "answer key" (tests and invariants) *before* any code is generated.

**Prompt for Claude:**
```
Read CLAUDE.md. Create src/p2p/schemas.py with JSON Schemas (strict, additionalProperties
false) for PLAN and BUILD, and src/p2p/prompts.py with compact prompts (target: system
prompt ≤ 700 tokens; no examples copied from any paper; one tiny NEUTRAL schema example
allowed).
PLAN schema fields: concept (string), why_it_matters, audience_notes,
source: {title, section_label, equation_label, equation_text_as_in_excerpt},
grounding_quotes: [≤4 short verbatim quotes from the excerpt],
symbols: [{symbol, meaning, shape_or_units}],
state: [{id, kind: number|vector|matrix|bool|choice, default, min, max, step, rows, cols}],
must_show_intermediates: [strings], outputs: [{key, meaning}],
tests: [≥4 {name, state_overrides (object), expect: {output_key: number|[numbers]},
        tol, rationale}], invariants: [{name, js_expression_over_out_and_state}],
explorations: [2× {change, observe, why}], limitation: {kind: limitation|assumption|
misconception, text}, simplifications: [strings].
Rules in the prompt: use ONLY the excerpt and focus; if the excerpt lacks something, put it
under simplifications; tests must cover the learning outcomes named in the focus, include
edge cases (zeros, extremes, ties) and use hand-checkable values; keep inputs small.
Implement src/p2p/plan.py: plan(case, llm, budget, trace) with max_tokens 1800; validate
against the schema in Python (jsonschema is NOT required—write a minimal validator or add
jsonschema pinned); trace stage="plan".
```

**Verify:**
```
python agent.py --input cases/a_attention.json --output runs/a --model "$MODEL_ID" --stop-after plan
python -c "import json;p=json.load(open('runs/a/plan.json'));print(len(p['tests']),p['source'])"
grep '"stage": "plan"' runs/a/trace.jsonl
```
Pass for case A: the plan names Section 3.2.1 and the equation softmax(QKᵀ/√d_k)V, Eq. (1). It includes tests where every attention-weight row sums to 1 and where output = weights·V, plus a test with equal scores giving uniform weights. Pass for case B: the plan names Section 6 and H = −K Σ pᵢ log pᵢ (Theorem 2).\[16\] It includes tests for certainty → 0 bits, uniform-4 → 2 bits, and p=0 contributing 0. Each plan call should use ≤1,500 completion tokens.

**Git:** `git commit -am "feat: plan schema + grounded planning call with pre-registered tests" && git tag -a v0.4-plan && git push --follow-tags`.

### Stage 5 — The BUILD call: content + `compute()` (45 min)

**Goal:** one call that produces everything concept-specific, in a compact form.

**Prompt for Claude:**
```
Read CLAUDE.md, src/p2p/schemas.py, and src/p2p/template/runtime.js (the SPEC/COMPUTE
contract). Implement src/p2p/build.py: build(case, plan, llm, budget, trace).
BUILD schema: spec (exactly the SPEC contract the runtime consumes: sections text in
HTML-lite, controls, visuals, output formatting, grounding block with
from_excerpt[] and our_simplifications[]), compute_js (string: ES2017, pure function
compute(state), no DOM, no Math.random unless seeded, no network, no globals besides
helpers it defines; must return intermediates listed in plan.must_show_intermediates and
compute self-checks for plan.invariants).
Prompt: give the model the PLAN (minified JSON), the case context, and the contract
summary (≤400 tokens). Instruct: audience-appropriate language; explain every symbol;
numbers on the page must come from compute(); controls must map to plan.state ids;
handle edge cases (zero probabilities, empty rows, division by zero) explicitly with
notes; explorations reference concrete control ids and values; do not claim to reproduce
paper results. max_tokens 7000 (clamped by budget). On Truncated: retry once with
instruction "be more concise; shorten text fields" and max_tokens +1500 if budget allows.
assemble.py combines template + spec + compute_js into out/index.html.
```

**Verify:**
```
python agent.py --input cases/a_attention.json --output runs/a --model "$MODEL_ID" --skip-checks
python -m http.server -d runs/a 8001     # open in Chromium (offline mode), operate controls
python tools/token_report.py runs/a/trace.jsonl   # totals per call
```
Pass: the page loads offline, all 7 sections are present, ≥2 controls change the visual *and* the numbers, and the intermediate values are visible. Case A should show scores, weights and output with a scaling toggle. The build call should stay ≤7k completion tokens and the run so far ≤12k total tokens. Read the page as a student would: are the symbols explained, and is the paper text clearly separated from your own simplifications?

**Git:** `git commit -am "feat: build call producing SPEC + pure compute module; assembly" && git tag -a v0.5-build && git push --follow-tags`.

### Stage 6 — Deterministic checks (45 min)

**Goal:** real, token-free checks that catch broken JS, made-up numbers, missing rubric items and network use.

**Prompt for Claude:**
```
Read CLAUDE.md. Implement src/p2p/checks.py: run_checks(case, plan, build, html) ->
list[CheckResult(name, passed, severity: critical|major|minor, detail, target_field)].
Static (on spec + final html):
- offline: no http(s):// in src/href/action/url()/@import/fetch/XMLHttpRequest/WebSocket/
  EventSource/import(); the only allowed URL occurrence is source_url as escaped text
- single file; size < 400 KB; no <iframe>, <object>, <embed>, external <script src>
- required sections present & non-empty: idea, symbols (≥2 rows), playground, explore-1,
  explore-2, limitation, grounding (with both from_excerpt and our_simplifications)
- ≥2 controls whose ids appear in plan.state and are read inside compute_js
- grounding: each grounding quote must appear in the excerpt after whitespace/Unicode
  normalization (if no excerpt is present, mark this check "skip" not "pass")
- section/equation label from plan appears in the grounding block
- disclaimer about not reproducing experimental results present
Numeric (QuickJS via `import quickjs`; fall back to py_mini_racer if import fails; if
both fail, mark numeric checks "skip" with reason and DO NOT claim pass):
- compute_js compiles; set_time_limit(2), set_memory_limit(64MB)
- compute(defaults) returns finite numbers for all outputs/intermediates
- each plan.test: merge state_overrides into defaults, compare outputs within tol
  (arrays elementwise); report expected vs got
- each plan.invariant evaluated on defaults + 5 deterministic perturbations
  (min, max, zeros, ties, random-but-seeded) -> must hold
- edge sweep: every control at min and max -> no exception, no NaN unless spec notes it
Also add a generic Python cross-check hook: if plan.tests include an expected value that
is itself inconsistent with invariants (e.g. weights not summing to 1), flag the TEST as
suspect (severity major, target_field "plan.tests") rather than blaming compute.
Emit one trace.check event per check. Unit-test with the neutral fixture and with
deliberately broken compute_js strings.
```

**Verify:**
```
python -m pytest -q tests/test_checks.py
python agent.py --input cases/b_entropy.json --output runs/b --model "$MODEL_ID" --no-repair
python tools/trace_view.py runs/b/trace.jsonl --checks    # table of check name / pass / detail
```
Pass: a deliberately broken fixture fails the expected checks, for example a compute that ignores the scaling toggle, a page with an external `<script src>`, or a quote that is not in the excerpt. Case B passes the certainty=0, uniform-4=2.000 and p=0 tests. Numeric checks print expected vs actual values. A missing JS engine shows up as `skip`, never as `pass`.

**Git:** `git commit -am "feat: static + executed numeric checks with pre-registered tests" && git tag -a v0.6-checks && git push --follow-tags`.

### Stage 7 — Targeted repair loop, best-version selection, exit codes (30 min)

**Goal:** revise only when a check fails, only what failed, and always stop within limits.

**Prompt for Claude:**
```
Read CLAUDE.md. Implement src/p2p/repair.py and the orchestration in agent.py.
Loop: after build -> checks. If no critical/major failures: finish. Else up to 2 repair
rounds while budget.can_call(3000) and time remains > 90s:
- Send ONLY: the failing checks (name, detail, expected vs got), the target field(s)
  current content (e.g. compute_js, or spec.sections.explore-2), and the relevant plan
  part. Ask for a JSON patch {field_path: new_value} (schema-constrained), max_tokens 3000.
- If a failure is flagged "suspect test", allow the patch to correct plan.tests but
  require a one-line rationale; record it in trace as revision.reason.
- Apply patch, re-run checks, trace.revision(round, targets, reason, before_fail_count,
  after_fail_count).
Keep every version; choose the best = fewest critical, then major, then minor failures.
Always write out/index.html from the best version (a partial page earns partial credit).
Exit 0 if a page was written and it has zero critical failures; else exit 1 (still keep
the page). Emit trace.summary with: calls, total prompt/completion/reasoning/cached
tokens, wall time, checks passed/failed, revisions, final_version, exit_code.
Global safety: a watchdog (threading.Timer) at 570s writes the best available page,
summary, and exits.
```

**Verify:**
```
python agent.py --input cases/a_attention.json --output runs/a2 --model "$MODEL_ID" --inject-fault compute   # dev flag: corrupts compute_js after build
python tools/trace_view.py runs/a2/trace.jsonl --revisions
echo $?
```
Pass: the injected fault leads to ≥1 repair round, which fixes it, and the trace shows the before/after failure counts. A healthy run makes 0 repair calls. Total calls never exceed 8 and wall time stays under 9 minutes, even with a deliberately unreachable model or simulated 429s.

**Git:** `git commit -am "feat: targeted JSON-patch repair, best-version selection, watchdog, exit codes" && git tag -a v0.7-repair && git push --follow-tags`. This is your **first submittable version**; tag it `rc1` as well.

### Stage 8 — Test harness, practice cases, token and latency tuning (60 min)

**Goal:** prove the agent handles new cases unchanged, twice each, in fresh output folders, then shrink tokens.

**Practice cases to create in `cases/`.** Write each excerpt by copying the relevant passage from the real paper; never write explanation content into a case. Fields: `source_url`, `title`, `section`, `excerpt`, `focus`, `audience`.
- `a_attention.json`: source https://arxiv.org/html/1706.03762v7, Section 3.2.1. The excerpt is the paragraph defining queries, keys and values of dimension d_k and d_v, Eq. (1) Attention(Q,K,V)=softmax(QKᵀ/√d_k)V, and the sentence explaining why the √d_k scaling counteracts large dot products pushing softmax into small-gradient regions. The focus is copied from the public example A brief.
- `b_entropy.json`: the Harvard-hosted Shannon PDF, Section 6, "Choice, Uncertainty and Entropy". The excerpt covers the three properties, Theorem 2 (H = −K Σ pᵢ log pᵢ, where K sets the unit) and the remarks that H=0 only when one pᵢ is 1 and H is maximal at log n for equal probabilities.\[16\] The focus is copied from example B.
- 3–5 self-made cases of comparable scope that are not in the public examples. Suggestions: softmax with temperature (Hinton et al., "Distilling the Knowledge in a Neural Network", arXiv 1503.02531, Sec. 2); convolution output size with padding and stride (Dumoulin & Visin, "A guide to convolution arithmetic", arXiv 1603.07285); the batch-normalization transform (Ioffe & Szegedy, arXiv 1502.03167, Algorithm 1); the Adam update with bias correction (Kingma & Ba, arXiv 1412.6980, Algorithm 1). Add one deliberately awkward case: no `excerpt` field, extra unknown fields and a very long excerpt.

**Prompt for Claude:**
```
Read CLAUDE.md. Write tools/harness.py: for each cases/*.json, run `python agent.py
--input CASE --output runs/<case>/<run_k> --model MODEL` twice (fresh dirs), as a
subprocess with a 600s timeout and a clean env containing only PATH and
OPENROUTER_API_KEY. Collect: exit code, wall time, calls, total tokens (prompt+completion
incl. reasoning & cached), checks pass/fail, revisions, html size, offline-scan result.
Print a markdown table + per-case mean, and save runs/summary.json. Flag any run with
calls>8, tokens>20k, time>300s, exit!=0, or any critical failure. Also add
tools/offline_scan.py (re-usable static offline/URL scan on any html).
Do NOT add any case-specific logic to agent code.
```

**Verify and tune:**
```
python tools/harness.py --model "$MODEL_ID"
```
Pass: 10+ runs across 5+ cases, all exit 0, no critical failures, consistent behaviour between the two runs of each case, a median under ~15k total tokens, and a median under ~120 s. Then tune in this order:
1. Shorten the system prompts.
2. Minify the JSON you send: send the plan, not the case, to repair.
3. Lower `max_tokens` to what you actually observe plus 30%.
4. Set the reasoning cap for your model.
5. Drop low-value text fields.
Re-run after every change, and keep a change only if quality stays equal. Have one teammate review each page with the rubric open: accuracy, clarity, visual, interaction.

**Git:** commit each tuning change separately (`perf: trim build prompt (-1.2k tokens, same checks)`), then `git tag -a v0.8-tuned && git push --follow-tags`. Commit `runs/summary.json` to `docs/` if it's useful, but keep `runs/` itself ignored.

### Stage 9 — README, example pair, final submission (25 min)

**Prompt for Claude:**
```
Read CLAUDE.md. Write README.md (concise): team members (placeholders I will fill),
one-paragraph summary, architecture diagram (text) and loop description, budgets and
safeguards, setup (exact commands: python -m pip install -r requirements.txt;
export OPENROUTER_API_KEY=...; python agent.py --input case.json --output out
--model <MODEL_ID>), the exact MODEL_ID line ("MODEL_ID: <id>"), input format (required +
optional fields), outputs (index.html, trace.jsonl schema table), checks performed,
limitations, reuse credits (libraries with licenses: requests, quickjs-ng; any snippet
or palette sources; AI coding assistant use), and a pointer to examples/.
Copy one harness run into examples/attention/ (case.json, index.html, trace.jsonl)
unchanged. Do not edit generated files by hand.
```

**Verify (fresh clone, as the instructor would):**
```
cd /tmp && rm -rf fresh && git clone <REPO_URL> fresh && cd fresh
python3.11 -m venv v && . v/bin/activate && python -m pip install -r requirements.txt
python agent.py --input examples/attention/case.json --output out --model "<MODEL_ID>"; echo $?
python tools/offline_scan.py out/index.html
```
Pass: a clean install, exit 0, an offline scan with no findings, and a page that works in Chromium in Offline mode.

**Git and submission:** `git commit -am "docs: README, example pair" && git push && git tag -a submission -m "final" && git push --tags`, then follow the checklist below.

## Time Budget (6-hour hackathon)

| Stage | Content | Minutes | Cumulative |
|---|---|---|---|
| 0 | Repo, env, secret guard | 15 | 0:15 |
| 1 | CLI, loader, trace, budget | 25 | 0:40 |
| 2 | OpenRouter client, pick MODEL_ID | 30 | 1:10 |
| 3 | Generic template + widgets (parallel with 2 in a team) | 50 | 2:00 |
| 4 | Plan schema + call | 25 | 2:25 |
| 5 | Build call + assembly | 45 | 3:10 |
| 6 | Deterministic checks | 45 | 3:55 |
| 7 | Repair loop, exit codes (rc1) | 30 | 4:25 |
| 8 | Harness, practice cases, tuning | 60 | 5:25 |
| 9 | README, example, freeze, submit | 25 | 5:50 |
| — | Buffer | 10 | 6:00 |

If a 2–3 person team runs Stages 2 and 3 in parallel, about 30 minutes moves into Stage 8 tuning, which is where token-efficiency points come from. **Hard rule:** if you are behind at 4:30, skip remaining polish and go straight to Stage 9 with `rc1`.

## Key Risks and Mitigations

| Risk | Mitigation |
|---|---|
| **Network limited to OpenRouter**, so fetching arXiv fails or hangs and wastes latency | Never fetch at run time. Use `excerpt` and other fields from case.json. If there is no excerpt, mark grounding checks `skip`, tell the model to label statements as from the focus or general knowledge, and say so on the page. |
| **Reasoning-token blow-up** or empty content with `finish_reason: length` | Choose a MODEL_ID whose reasoning can be capped. Set `reasoning.effort` low/none or `reasoning.max_tokens`. Detect starvation (`completion − reasoning < 50`). Clamp `max_tokens` against the remaining budget. |
| **Broken JS / dead controls** | The template owns all DOM code. The LLM writes only a pure `compute()`, which the checks compile and execute. The runtime shows an error box instead of a blank page. |
| **Hallucinated numbers** | Every number on the page comes from `compute()`. Tests are fixed in the plan before code exists. Invariants are re-checked on perturbed inputs, and an inconsistent test is flagged as "suspect" rather than blindly "fixed". |
| **Mis-grounding / overclaiming** | Verbatim quotes are substring-checked against the excerpt. Paper text and your simplifications are rendered in separate lists. A fixed disclaimer says the demo does not reproduce experimental results. |
| **case.json field mismatch** | Require only the three named fields, keep and forward all other string fields, and never crash on unknown types. |
| **JSON schema not honoured / malformed JSON** | Use `require_parameters`, `response-healing`, a local validator, and one concise retry on truncation.\[6\]\[17\] |
| **Rate limits / 5xx** | Retry only 408/429/502/503, at most 2 times, honouring `Retry-After`. All attempts count toward the cap of 8. |
| **Engine wheel unavailable on assessor OS** | Try `quickjs` first, then `py_mini_racer`, then mark numeric checks as skipped. The agent never crashes on import, and both packages are pinned. |
| **Leaked API key** | Use `.gitignore`, a pre-commit scanner and GitHub push protection, and read the key from env only.\[14\]\[18\] If a key ever lands in git, revoke it on OpenRouter immediately; removing it from history is not enough.\[14\]\[19\]\[20\]\[21\] |
| **Reward hacking** | Do not put text in the page or repo aimed at the assessor, do not use cached responses, and report usage honestly. |

## Final Submission Checklist

1. `git status` is clean and `git log --oneline -5` shows your last commit.
2. Run `git push`, then record the SHA with `git rev-parse HEAD`, which gives the full 40 characters. Confirm it matches `git ls-remote origin refs/heads/main`.
3. Make sure the instructor can read the repo. Either make it public, or invite the instructor as a collaborator. GitHub Docs ("Permission levels for a personal account repository") state that "Collaborators can't have read-only access to repositories owned by a personal account," so use a free organization if you need read-only access. Check that an incognito window (if public) or the instructor's account can see the commit page.
4. `agent.py` is at the repo root, and `requirements.txt` pins every package with `==`. Nothing needs a system package, a browser download or a GPU.
5. A fresh clone plus `pip install -r requirements.txt` plus the exact CLI works and exits 0, using the README's MODEL_ID.
6. The README contains team members, the architecture, setup, reuse credits (libraries, snippets, AI assistants) and the line `MODEL_ID: …`.
7. `examples/<case>/` contains `case.json`, `index.html` and `trace.jsonl` exactly as generated.
8. `grep -rn "sk-or-" .` returns nothing, and no `.env` is tracked (`git ls-files | grep -i env` shows only `.env.example`).
9. The trace has per-call prompt/completion/reasoning/cached tokens, elapsed seconds, checks, failures, revisions and a summary. It contains no message bodies, reasoning or credentials.
10. Submit the repo URL and the full SHA before the session ends. That commit is final, so don't push "one more fix" without updating the SHA.

## Caveats

- The spec's "five required string fields" names only three. The roadmap assumes an `excerpt` field and maybe others; the loader tolerates either.
- The token, latency and size targets above (≤15k tokens, ≤120 s) are engineering targets, not measured results. Calibrate them with your harness and your chosen model, since tokenizer and verbosity differ widely between models.
- The `quickjs-ng` facts come from its 0.16.2.1 PyPI page. A 0.17.0.1 release exists but was not checked; pin the version you test. `quickjs-ng` has no macOS-Intel wheel; if a teammate is on an Intel Mac, also pin `mini-racer` and rely on the import fallback.\[22\]
- OpenRouter features (plugins, structured outputs, reasoning parameters) vary by model and provider.\[6\] Re-verify with `tools/ping.py` on the day.
- Whether you may prepare generic scaffolding before the hackathon is a course-policy question. Ask the instructor first, and note any pre-prepared generic code in the README credits.

## Sources

1. [Usage Accounting - Track AI Model Token Usage](https://openrouter.ai/docs/cookbook/administration/usage-accounting)
2. [OpenRouter API Reference - Complete Documentation](https://openrouter.ai/docs/api_reference/overview)
3. [openrouter-generations skill for OpenRouter](https://openrouter.ai/skills/openrouter-generations)
4. <https://openrouter.ai/docs/guides/best-practices/reasoning-tokens>
5. [Structured Outputs - Type-Safe JSON Responses from AI Models](https://openrouter.ai/docs/guides/features/structured-outputs)
6. [OpenRouter Structured Output: Why Your Schema Gets Ignored](https://aireiter.com/blog/openrouter-structured-output-guide)
7. [Response Healing - Fix Malformed JSON from AI Models](https://openrouter.ai/docs/guides/features/plugins/response-healing)
8. [Response Healing: Reduce JSON Defects by 80%+](https://openrouter.ai/blog/announcements/response-healing-reduce-json-defects-by-80percent/)
9. <https://openrouter.ai/docs/api_reference/errors-and-debugging>
10. [Response Caching - Cache Identical API Responses](https://openrouter.ai/docs/guides/features/response-caching)
11. [python -m playwright install: Fix Browser Install Errors](https://qaskills.sh/blog/python-playwright-install-fix-browser-errors)
12. [playwright install chromium: Guide and Fixes](https://spyderproxy.com/blog/playwright-install-chromium)
13. [esprima · PyPI](https://pypi.org/project/esprima/)
14. [How does GitHub handle exposed secrets or credentials in public repos? · community · Discussion #161907](https://github.com/orgs/community/discussions/161907)
15. [GitHub Secret Scanning & Push Protection: A Guide to Secure Your Git Repositories](https://devactivity.com/insights/securing-your-codebase-mastering-github-secret-scanning-push-protection-with-git-repo-analysis-tools/)
16. [Reprinted with corrections from The Bell System Technical Journal,](https://people.math.harvard.edu/~ctm/home/text/others/shannon/entropy/entropy.pdf)
17. [OpenRouter Structured Output Broke Before Translation Quality Did — 3 Layers of Defense for Production - DEV Community](https://dev.to/lovanaut55/openrouter-structured-output-broke-before-translation-quality-did-3-layers-of-defense-for-1cdb)
18. [Secrets Scanning with Gitleaks, TruffleHog, and GitHub (2026)](https://www.decryptiondigest.com/blog/secrets-scanning-pre-commit-ci-enforcement)
19. [API Authentication - Secure Access to OpenRouter](https://openrouter.ai/docs/api_reference/authentication)
20. [security: purge leaked OpenRouter API key from git history (Alert #2) · Issue #1723 · tosin2013/mcp-adr-analysis-server](https://github.com/tosin2013/mcp-adr-analysis-server/issues/1723)
21. [Secret Scanning with Gitleaks](https://labs.iximiuz.com/courses/devsecops-security-hands-on-9dc87991/module-11/gitleaks)
22. [quickjs-ng](https://pypi.org/project/quickjs-ng/0.16.2.1/)
