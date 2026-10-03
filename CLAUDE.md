# Project rules: paper-to-playground

- Python 3.11. `agent.py` stays at the repo root. Package code lives in `src/p2p/`.
- CLI: `python agent.py --input case.json --output out --model <MODEL_ID>`.
- All LLM calls go through OpenRouter: `POST https://openrouter.ai/api/v1/chat/completions`
  with header `Authorization: Bearer $OPENROUTER_API_KEY`. Read the key from the env only.
- Never print, log or commit the API key. No `.env` in git.
- Per-case limits: ≤10 API requests (retries included), ≤30,000 completion tokens, ≤10 minutes.
- Network at assessment time is OpenRouter only. Never fetch `source_url`; use case.json fields.
- Outputs: `out/index.html` (one self-contained file: no CDN, no remote fonts/images,
  no external URLs) and `out/trace.jsonl`.
- No paper-specific code, lookup tables or prewritten pages. Generic templates only.
- Dependencies: pin every package with `==` in requirements.txt. No system packages,
  browser downloads or GPU at run time. Playwright is dev-only.
- Full assignment: `docs/SPEC.md`. Build plan: `docs/ROADMAP.md`. Read both before each stage.
- Secret guard: run `python tools/install_hooks.py` once after cloning.
