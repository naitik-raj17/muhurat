# Build Prompt: AI Operating Layer for Wedding Planning (CATI AI Assignment)

You are a senior AI/backend engineer. Build a working, runnable implementation of the system described in my design document "AI-Powered Wedding Planning System" (attached as a PDF, if present in this folder). This file is the source of truth for scope. Where this file and the PDF differ, follow this file. The differences are deliberate corrections.

## 0. How you must work (read first)

1. **Plan before coding.** Read this whole file (and the PDF if present). Then reply with a numbered build plan and the full folder structure. Wait for my "go" before writing code.
2. **Build in phases** (Section 9). Finish one phase, run it, run its tests, show me the results, then continue. Do not start a phase while the previous one is failing.
3. **Never fake success.** If something fails, say so and fix it. Do not mark a step done unless you actually ran it.
4. **Do not invent APIs.** If you are unsure of a library's current API or a model's current name, check the installed package or official docs instead of guessing. Pin dependency versions.
5. **Everything must run offline by default.** Every external service (LLMs, WhatsApp, Gmail, Calendar, Razorpay, vendor marketplaces, Pinecone) sits behind an interface with a **mock implementation that is the default**. Real implementations activate only when the matching keys exist in `.env`.
6. **No secrets in code.** Use `.env` plus a committed `.env.example` with every variable documented.
7. **Ask me** before any decision that is not covered here and is hard to reverse. For small ambiguities, choose the sensible option and note it in `DECISIONS.md`.

## 1. Project goal

An AI operating layer for wedding planning companies in India that automates:

1. **Vendor Discovery & Contracting** (core workflow, build in most depth): match vendors by style, budget, location, date availability; benchmark prices; extract contract clauses and flag risks. Assistive only: AI recommends, a human approves.
2. **Guest Management & RSVP**: import guest lists (CSV/Excel), send invites and reminders, track RSVPs, detect family groups, predict attendance. Agentic (Tier 1).
3. **Timeline & Logistics**: generate multi-day timelines (Mehendi, Haldi, Sangeet, Wedding, Reception) with buffer zones, detect conflicts, propose reschedules and notify vendors within guardrails. Agentic (Tier 1–2).
4. **Budget**: track expenses, forecast spend, send budget alerts.

## 2. Tech stack

| Layer | Use |
|---|---|
| Backend | Python 3.11+, FastAPI (async), Pydantic v2, SQLAlchemy 2.x + Alembic |
| Database | PostgreSQL 16 with the **pgvector** extension (runs locally in Docker) |
| Vector search | `VectorStore` interface: **pgvector implementation (default)**, Pinecone implementation (optional, enabled when `PINECONE_API_KEY` is set) |
| Cache | Redis (rate limiting, semantic cache, sessions) |
| Background jobs | **One job system at a time, never two.** Phase 1–2: FastAPI background tasks plus the outbox dispatcher (Section 3A). Phase 3: Temporal takes over all scheduled and long-running jobs (reminders, bulk sends, wedding workflows). Do **not** add Celery |
| Workflow / agents | Temporal (Python SDK) for durable, long-running wedding workflows. Add it in Phase 3, not before |
| Object storage | MinIO (S3-compatible) for contracts and uploaded files |
| Frontend | React + TypeScript + Vite, simple clean dashboard |
| Observability | Structured JSON logging; Prometheus `/metrics` endpoint; Sentry SDK (disabled when no DSN) |
| Tests | Backend: pytest + pytest-asyncio, httpx test client, at least one end-to-end test per workflow. Frontend: Vitest for components, Playwright for key flows (login, vendor shortlist, approval queue) |
| Code quality | ruff (lint + format), mypy (strict on `app/domain` and `app/services`), ESLint + Prettier + `tsc --noEmit` for the frontend, pre-commit hooks running all of these |
| CI | GitHub Actions: on every push and pull request run lint, type checks, backend tests (with Postgres + Redis service containers) and frontend tests. Build must be green before a phase counts as done |
| Dev environment | `docker compose up` starts everything; a `Makefile` with `make dev`, `make test`, `make seed`, `make lint`, `make typecheck` |

Kong API gateway from the PDF is **not** required for the build. Implement auth (JWT), rate limiting (Redis) and routing inside FastAPI, and mention Kong as the production option in the README.

## 3A. Architecture principles (mandatory)

1. **Layered / ports-and-adapters structure.** Backend layout:
   - `app/api/v1/` – HTTP routes only (thin; validation + calling services)
   - `app/services/` – use cases and business logic
   - `app/domain/` – entities, Pydantic schemas, policies (no framework or DB imports)
   - `app/adapters/` – implementations of external interfaces (LLM, messaging, vector store, storage, travel time, payments)
   - `app/repositories/` – all DB access
   - `app/agents/`, `app/workflows/` – agents and Temporal workflows (Phase 3)
   Dependencies point inward: routes → services → domain. Domain never imports adapters. Use FastAPI dependency injection to wire implementations.
2. **Multi-tenancy (B2B).** Every wedding planning company is an `organization`. Every tenant-owned table has a non-null `organization_id`, indexed. All repository queries are scoped by the current user's organization automatically (a base repository that always applies the filter), never by remembering to add a `WHERE` in each query. Vendors may be a shared global catalogue plus org-private vendors; shortlists, contracts, guests, budgets and all wedding data are strictly org-private. Write tests proving one organization cannot read or modify another's data through any endpoint.
3. **RBAC.** Roles: `org_admin` (manage users, settings, approve Tier 2 overrides), `planner` (run weddings, approve recommendations), `viewer` (read-only staff), `client` (the couple: read-only access to their own wedding, can submit RSVPs/preferences). Enforce permissions in a single dependency/policy layer, not scattered checks.
4. **API versioning.** All routes under `/api/v1/`. Publish the OpenAPI schema and generate the frontend API client from it (e.g. `openapi-typescript`), so frontend and backend types never drift.
5. **Idempotency.**
   - Incoming webhooks (WhatsApp, email, payments) are deduplicated by provider message ID, stored in an `idempotency_keys` table with a unique constraint.
   - Mutating POST endpoints accept an `Idempotency-Key` header; repeating the same key returns the original result.
   - Outgoing messages carry a deterministic dedupe key (guest + event + template + reminder number) so a retry never sends a guest the same reminder twice.
6. **Transactional outbox.** Never call an external service inside a DB transaction. Write the business change and an `outbox_events` row in the same transaction; a dispatcher reads the outbox and performs the external call with retries and exponential backoff, marking events sent or failed. Failed events after max retries appear in an admin view.
7. **Consistent errors.** One error format (RFC 9457 problem details) across all endpoints. Structured logs carry a request ID / correlation ID that also flows into outbox events and agent actions.
8. **Health checks.** `/health/live` and `/health/ready` (checks DB, Redis, storage).

## 3. AI models: corrections to the PDF

- **Do not hardcode model names.** All model IDs live in `config/models.yaml`, mapped by task: `guest_comm`, `contract_analysis`, `timeline_reasoning`, `general`. The PDF names Gemini 1.5 Flash/Pro and GPT-4o-mini. Gemini 1.5 models are retired, so use current equivalents and put them in config only.
- **LLM access** goes through one `LLMClient` interface with three implementations: `MockLLM` (default, deterministic, used in tests), `OpenRouterLLM` (OpenAI-compatible API, enabled by `OPENROUTER_API_KEY`), and room for direct providers later.
- **Model router**: chooses cheap/fast models for simple tasks and the stronger model only for timeline reasoning and contract analysis. Log chosen model, tokens and estimated cost for every call.
- **Embeddings**: run locally with `sentence-transformers`. Use **`paraphrase-multilingual-MiniLM-L12-v2`**, not `all-MiniLM-L6-v2`, because the latter is English-only and the system must handle Hindi and other Indian languages. Make the model configurable.
- **Structured output**: every LLM output that the system acts on is validated against a Pydantic schema. On validation failure, retry once with the error message, then fall back to rule-based logic and flag for human review.
- **Budget forecasting**: start with a simple scikit-learn regression trained on seed data (not an LLM). Leave a hook for Vertex AutoML later.

## 4. Data model (minimum)

`organizations`, `users` (organization_id, role from Section 3A), `weddings`, `events` (per wedding: mehendi, sangeet, etc., with date, venue, start/end), `vendors` (category, city, price range, styles, languages, rating, description, embedding), `vendor_availability`, `vendor_shortlists`, `contracts` (file, extracted clauses JSON, risk flags), `guests` (name, phone, email, language, side, group_id, rsvp_status per event), `messages` (channel, template, status), `timeline_items`, `budget_items`, `agent_actions` (audit log: agent, action, tier, input, output, status, approved_by, timestamps), `approval_requests`, `token_usage` (per user per month), `outbox_events`, `idempotency_keys`.

Every tenant-owned table has `organization_id`. All tables have `created_at` / `updated_at`; use UUID primary keys.

Use Alembic migrations. Never edit the DB schema without a migration.

## 5. Workflow details

### 5.1 Vendor Discovery (core)
- **Hybrid search**: BM25 keyword search (Postgres full-text) plus dense vector search, combined with **Reciprocal Rank Fusion**. Then filter by hard constraints (city, date availability, budget) and rank.
- Every recommendation shows **why** (matched styles, price vs. budget, availability, rating). This is the explainability requirement.
- **Price benchmarking**: for a vendor category and city, show median, p25 and p75 from the data, and where a quote sits.
- **Contract analysis**: upload PDF/DOCX → extract text → LLM extracts clauses (payment schedule, cancellation, refund, liability, force majeure, deliverables) into a Pydantic schema → rule-based risk flags (e.g. non-refundable advance > 50%, no cancellation clause). Every extracted clause must quote the source text span, and the system checks that the quote really exists in the document. If it doesn't, flag it as a possible hallucination.
- Final vendor selection always requires a human click.

### 5.2 Guest Management
- CSV/XLSX import with validation and a clear error report per row (bad phone numbers, duplicates).
- Phone numbers normalised to E.164 (use the `phonenumbers` library).
- Message templates per language (at least English and Hindi seeded). Channels: WhatsApp, email, behind a `MessagingChannel` interface with mock as default.
- Reminder rules: configurable (e.g. reminder after N days without reply, max M reminders). Respect quiet hours (no messages 21:00–09:00 IST).
- RSVP updates via a webhook endpoint (simulate replies in mock mode). The webhook verifies the provider signature and is idempotent (Section 3A).
- All outgoing invites and reminders go through the outbox, never sent directly from a request handler.
- Attendance prediction: simple model or heuristic from RSVP status, side, distance, past response; show an expected headcount range per event.

### 5.3 Timeline & Logistics
- Generate a draft multi-day timeline from wedding events and booked vendors, with configurable buffers between items.
- Conflict detection: overlapping items, a vendor booked in two places, setup time missing, travel time between venues (use a mock travel-time provider by default).
- Propose fixes; apply them only according to the autonomy tiers below.

### 5.4 Budget
- Track planned vs. actual per category. Alert when a category passes a threshold (e.g. 90%). Forecast final spend.

## 6. Autonomy tiers and guardrails (must be enforced in code, not just prompts)

Create one `ActionPolicy` module. Every agent action passes through it before execution.

| Tier | Actions | Rule |
|---|---|---|
| Tier 1 – autonomous | send RSVP reminders, update guest status, generate timeline drafts, suggest vendors, send budget alerts | Execute and log |
| Tier 2 – bounded | reschedule a vendor slot, adjust timelines, accept a negotiated price change | Execute only within limits from config: reschedule shift ≤ 48 hours, price change ≤ 5%. Outside limits → create an approval request. All Tier 2 actions must be reversible |
| Tier 3 – prohibited for AI | cancel contracts, modify legal terms, execute payments | Always blocked for agents. Only a human user can do these via the UI. Raise an error and log the attempt if an agent tries |

Note: the PDF says "reschedule vendors (within 48h)". Interpret this as "a shift of at most 48 hours", keep it in config, and record the interpretation in `DECISIONS.md`.

Other guardrails:
- **Token budget** per user per month (default 1M tokens, configurable). Warn at 80%, throttle at 100%.
- **Prompt injection defence**: guest replies, vendor descriptions and contract text are untrusted. Keep them clearly separated from instructions in prompts, never let them trigger actions directly, and strip/escape suspicious instructions.
- **PII**: mask phone numbers and emails in logs. Encrypt phone/email columns at rest (application-level encryption with a key from `.env`). Note DPDP Act 2023 considerations in the README.
- **Audit + rollback**: every agent action is written to `agent_actions` with before/after state, so Tier 2 actions can be undone from the UI.
- **Fail-safe**: low confidence or validation failure → escalate to a human approval request, never guess.

## 7. Agents

An **Orchestrator** that holds wedding state and dispatches to four agents: Vendor, Guest, Timeline, Budget. Each agent is a Python class with explicit tools (functions) it may call. Tools are registered with their tier, so the `ActionPolicy` can check them. In Phase 3, each wedding runs as a long-lived Temporal workflow with checkpoints at milestones (vendors booked, invites sent, timeline locked).

## 8. Frontend pages

Login · Weddings list · Wedding overview (status, budget, upcoming tasks) · Vendor search with explanations and shortlist · Contract upload with clause table and risk flags · Guests (import, table, RSVP status, send reminders) · Timeline (per-day view with conflicts highlighted) · Budget · **Approvals queue** · **Agent activity log** with "undo" for reversible actions. Clearly label which actions were done by AI vs. a human.

## 9. Phases (build in this order)

**Phase 1 – MVP (assistive only)**
Repo setup first: layered folder structure, pre-commit, ruff/mypy/ESLint, GitHub Actions CI. Then Docker compose (Postgres+pgvector, Redis, MinIO), FastAPI skeleton under `/api/v1`, health checks, error format, organizations + multi-tenant base repository, JWT auth + RBAC, DB models + migrations, seed script (2+ organizations), vendor search (keyword + rules first), guest import, basic timeline templates, mock LLM + mock messaging, generated TypeScript API client, React dashboard for these pages. Tenant-isolation tests and CI green.

**Phase 2 – Learning**
Embeddings + hybrid RRF search with explanations, price benchmarking, contract analysis with quote verification, budget forecasting model, outbox + dispatcher with retries, idempotent webhooks and endpoints, messaging channels with templates and reminder rules (FastAPI background tasks + outbox), OpenRouter client and model router with cost logging, token budgets, Playwright tests for key flows.

**Phase 3 – Autonomous**
Temporal replaces the Phase 2 background tasks for all scheduled jobs. ActionPolicy with tiers, approval queue, audit log + rollback, agents + orchestrator on Temporal, conflict detection and auto-fixes within limits, Prometheus metrics, Sentry hook, approvals and activity pages.

## 10. Seed data

Generate realistic **synthetic** data (no real people): 2 planning companies (organizations) with users in every role; ~300 vendors across categories (venue, caterer, decorator, photographer, makeup, mehendi artist, DJ, pandit) and cities (Delhi, Mumbai, Jaipur, Patna, Bengaluru, Hyderabad, Kolkata, Udaipur), prices in INR, descriptions in English and some in Hindi; 3 sample weddings; ~200 guests per wedding; 5 sample contracts (as generated PDFs) including some with risky clauses. Mark all seed data as synthetic.

## 11. Evaluation (from Section 2.7 of the PDF)

Add an `eval/` folder with scripts that run offline against seed data:
- Vendor matching: precision@5 and recall@10 on a labelled set of ~50 queries (create the labels from seed data rules).
- Contract extraction: field-level accuracy on the sample contracts with known answers.
- RSVP pipeline: correctness on synthetic guest lists.
- API latency: p95 for the main endpoints (target < 2 s).
- Cost: estimated LLM cost per wedding from `token_usage` (target < Rs. 300).
Output a short report as Markdown.

## 12. Definition of done

- `docker compose up` and `make seed` give a working app with no API keys set.
- `make test`, `make lint` and `make typecheck` pass locally, and CI is green on GitHub. Every workflow has at least one end-to-end test.
- Tests prove: one organization cannot access another's data; each role can only do what Section 3A allows; a duplicated webhook or repeated `Idempotency-Key` causes no duplicate effect; a failed external call leaves the outbox event retryable and the DB consistent.
- Tier 3 actions are impossible for agents (there is a test proving this).
- Tier 2 limits are enforced and reversible (tests prove this).
- `README.md` covers setup, architecture diagram (Mermaid), how to enable real providers, and known limitations.
- `DECISIONS.md` lists every assumption and deviation from the PDF.

Start now with step 0.1: read everything and send me the plan and folder structure. Do not write code yet.
