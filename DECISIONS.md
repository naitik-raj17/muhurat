# Decisions, assumptions and deviations

Each entry: what was decided, why, and what it deviates from (if anything).
The build prompt (`docs/BUILD_PROMPT.md`) is the source of truth; where the PDF differs, the prompt wins.

| ID | Decision | Why |
|---|---|---|
| D-001 | **The design PDF (`your-report.pdf`) was not available** at build time (searched the whole filesystem). Scope comes entirely from `docs/BUILD_PROMPT.md`. | The prompt says to use the PDF "if present" and to follow the prompt where they differ. |
| D-002 | Backend pinned to **Python 3.12** (via `uv`), run in `python:3.12-slim` containers. | The host has Python 3.14; PyTorch / sentence-transformers wheels are reliable on 3.12. The prompt asks for 3.11+. |
| D-003 | All dependencies pinned to exact versions (`pyproject.toml` + `uv.lock`, `package.json` + `package-lock.json`). GitHub Actions pinned to exact release tags. | Prompt rule 0.4. Reproducible builds. |
| D-004 | Model IDs live only in `config/models.yaml`: `fast` = `google/gemini-3.5-flash-lite` (guest_comm, general), `strong` = `google/gemini-3.1-pro-preview` (contract_analysis, timeline_reasoning), `fallback` = `openai/gpt-5-mini`. Verified against OpenRouter's live model list on 2026-10-16. | The PDF's Gemini 1.5 models are retired. No non-preview Gemini Pro exists on OpenRouter at this date, so the strong tier uses the 3.1 Pro preview with a stable fallback. Changing models is a config edit. |
| D-005 | **TypeScript pinned to 5.9.3**, not the Vite template's 6.0. | `openapi-typescript@7.13.0` (latest) declares `peer typescript@^5.x`. Forcing past a peer conflict risks a broken generated client. |
| D-006 | The Vite template's `oxlint` was replaced by **ESLint + Prettier**. | Prompt §2 names ESLint + Prettier. |
| D-007 | "Reschedule vendors (within 48h)" is interpreted as **a shift of at most 48 hours** (`config/policy.yaml: tier2_limits.max_reschedule_shift_hours`). | Prompt §6 instruction. |
| D-008 | `.env` inline comments moved to their own lines. | docker compose and python-dotenv parse inline comments differently. |
| D-009 | Pre-commit hooks are `repo: local` and call `uv run` / `npm run`. | Guarantees hooks use exactly the pinned tool versions instead of a second copy with its own versions. |
| D-010 | **`JOB_BACKEND` switch** (`inprocess` or `temporal`). docker compose uses `temporal`. With Temporal, the in-process outbox worker isn't started at all. | Prompt §2 says "one job system at a time, never two". The switch keeps `inprocess` as a no-extra-services option for local work and tests. |
| D-011 | **Approving a Tier 2 action outside its limits needs `approval:decide_override` (org admins).** Other approvals need `approval:decide` (planners). The Approvals page hides the buttons from users who lack the required permission. | The prompt doesn't say who approves. Going past configured limits is the riskier decision, so it gets the stronger role. |
| D-012 | **The request DB session commits before the response is sent** (`Depends(get_db, scope="function")`). | FastAPI's default dependency scope runs the commit after the response. Found through the Approvals page: a refetch right after "Approve" read stale data. A failed commit could also still return a 200. Covered by `tests/integration/test_unit_of_work.py`. |
| D-013 | **Playwright flows run locally against the live stack** (`make test-e2e` after `make up && make seed`), not in CI. | The flows need the whole stack (Temporal, worker, embeddings model). CI runs the backend e2e tests and Vitest instead. |
