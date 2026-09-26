# CLAUDE.md — ShopEase Copilot

## What this project is

An AI customer-support copilot for **ShopEase**, a fictional Indian online electronics and home-appliance store (INR, UPI, COD, GST invoices). The copilot answers policy questions (RAG), looks up orders and refund eligibility (tools), escalates to humans when needed, and drafts replies for agents.

The real point of the project is the **platform around the app**, built for a GenAI-engineer portfolio:

1. **LLM gateway**: self-hosted **LiteLLM Proxy**, configured and extended with custom callbacks (virtual keys, budgets, rate limits, fallbacks, cache, guardrails, cost tracking, complexity-based routing)
2. **Observability**: traces, metrics, dashboards, request logs
3. **Evaluation**: golden dataset, LLM-as-judge, judge calibration, model/prompt comparison
4. **LLMOps**: prompt registry, CI eval gate, canary rollout
5. **Multi-agent (phase 2)**: supervisor + specialists, benchmarked against the single-agent baseline

Every feature must be measurable. If it can't be measured, it isn't done.

## Working agreement (read first)

- **Plan before code.** For any task larger than one file, first write a short plan (files to touch, approach, risks) and wait for my approval.
- **One vertical slice at a time.** Get a request flowing end to end before adding features.
- **One step at a time, one file at a time.** Implement only the current step in `docs/STEP_PLAN.md` — usually a single file or a single section of a file. Stop and report back when it is done. Never continue to the next step on your own, even if the next step seems obvious.
- **Create only what the step needs.** No empty packages, stubs, placeholder files or unused folders.
- **Every configurable value is registered.** Anything I might want to change (ports, URLs, keys, model aliases, thresholds, counts, paths) goes in `.env` (secrets, ports, URLs), `gateway/config.yaml` (models, routing, fallbacks), `gateway/teams.yaml`, or `config/*.yaml` (behavior tunables), with a default. It must also be listed in `.env.example` and `docs/CONFIG.md` (name, purpose, default, where set, where used, example alternative). No magic numbers or hard-coded URLs in code.
- **Every step ends with a manual check.** Provide runnable check commands (prefer `scripts/check_*.py`), the expected output, and a "change one value and confirm behavior changes" test. Report the config values the step introduced.
- **Small commits.** One feature per commit, conventional-commit messages (`feat:`, `fix:`, `test:`, `docs:`, `chore:`).
- **Explain non-obvious decisions** in a short comment or in `docs/decisions.md`. I must be able to defend every choice in an interview.
- **Ask, don't guess,** when a requirement is ambiguous.
- **Never commit secrets.** API keys live in `.env` (gitignored). Keep `.env.example` up to date.
- **Don't add dependencies casually.** State why a new library is needed.
- **Don't refactor unrelated code** while implementing a feature.
- **Reuse before writing.** Before writing gateway code, check whether LiteLLM already supports it (keys, budgets, fallbacks, cache, guardrails, callbacks). Custom code is only for gaps, and each gap gets an entry in `docs/decisions.md`.

## Tech stack

| Layer | Choice |
|---|---|
| Language | Python 3.11+, managed with `uv` |
| Gateway | LiteLLM Proxy (self-hosted via Docker) + custom Python callbacks/hooks where needed |
| App framework | FastAPI (copilot), Pydantic v2, httpx (async) |
| Database | Postgres 16 with pgvector (relational data, embeddings, request logs) |
| Cache / rate limit | Redis |
| Observability | LiteLLM Prometheus metrics + Grafana; LLM trace inspection via self-hosted Phoenix (lightweight default) or Langfuse, through LiteLLM callbacks; OpenTelemetry for copilot spans. No SaaS trace tools |
| Models | **Local-first, zero budget.** Open-weight models via Ollama are primary. One or two free-tier hosted APIs are used only as fallback and judge. No paid APIs. Roles and hardware tiers are in `docs/ARCHITECTURE.md` |
| Evaluation | Custom runner + LLM-as-judge; RAGAS or DeepEval only where they save real time |
| UI | Streamlit for chat and eval report viewing (keep it thin) |
| Ops | Docker Compose, GitHub Actions, Locust for load tests |
| Quality | pytest, ruff, mypy (or pyright) |

Model names, prices and routing rules live in **config files** (`gateway/config.yaml`), never hard-coded in Python. Provider model names change often.

## Architecture

```
Chat UI (Streamlit)
   │
   ▼
copilot/ (FastAPI)  ── intent classifier, RAG retriever, tools, prompt registry
   │  OpenAI-compatible calls (base_url = gateway)
   ▼
gateway/ (LiteLLM Proxy) ── virtual keys → rate limits/budgets → guardrails → cache → router → fallback
   │                         + custom callbacks (complexity routing, PII/injection hooks, eval sampling)
   ├──► OpenAI / Anthropic / Ollama
   ├──► Prometheus → Grafana        (gateway metrics)
   ├──► Langfuse / LangSmith        (LLM trace inspection, via callback)
   └──► Postgres (spend logs, keys, teams)

evals/ ── offline golden-set runs + online sampling, reports, CI gate
```

**Hard rule:** the copilot never calls a provider directly. Every LLM call goes through the gateway. That is what makes observability and evaluation trustworthy.

## Folder structure (target end state)

> Do **not** create these up front. Create a folder only in the step that first needs it (see the table in `docs/STEP_PLAN.md`).

```
shopease-copilot/
├── CLAUDE.md
├── README.md
├── docker-compose.yml
├── pyproject.toml
├── .env.example
├── copilot/            # app: api.py, rag/, tools/, prompts/, agents/ (phase 2)
├── gateway/            # config.yaml, callbacks/ (custom routing, guardrails, sampling), README.md
├── observability/      # prometheus.yml, grafana/dashboards/, langfuse compose (if used)
├── evals/              # datasets/, runners/, judges/, reports/
├── data/               # policies/*.md, generators/, seed_db.py
├── ci/                 # eval_gate.py, thresholds.yaml
├── tests/              # unit/, integration/
└── docs/               # decisions.md, architecture.md, results.md, plan
```

## Conventions

- Type hints everywhere. Pydantic models for every request/response and config schema.
- Async I/O for anything network-bound.
- Structured JSON logging. Every request carries a `request_id` and `conversation_id`, propagated through gateway, copilot and traces.
- The gateway is LiteLLM Proxy. It exposes the OpenAI-compatible `/v1/chat/completions` (including streaming). Check the current LiteLLM docs before writing any custom gateway code.
- Cost tracking uses LiteLLM spend logs (Postgres). Add custom pricing in `gateway/config.yaml` for any model LiteLLM doesn't price, and convert to INR at report time.
- Attach metadata (team, agent name, prompt version, conversation id) to every call using LiteLLM's request metadata/tags (confirm the exact mechanism in the docs and record it in `gateway/README.md`). This powers per-team, per-agent and per-prompt breakdowns.
- Prompts are files in `copilot/prompts/<name>/vN.md` with a registry. Prompt text is never inlined in code.
- Tools are plain Python functions with a JSON schema, and they return structured results and typed errors.
- Config via environment variables (pydantic-settings). No magic globals.

## Commands

This section starts empty and is updated by you (not Claude Code) as commands become real, one at a time, as each step in `docs/STEP_PLAN.md` is completed and checked. Do not pre-fill it with commands for steps that haven't happened yet.

## Evaluation principles

- The golden set is **hand-reviewed**. Never auto-overwrite it. Schema is in `evals/datasets/SCHEMA.md`.
- Metrics: faithfulness, answer correctness, retrieval recall@k, tool-call accuracy, escalation recall, policy violations (target 0), latency p95, cost per conversation.
- The LLM judge must be **calibrated** against human labels (`evals/datasets/judge_calibration.jsonl`). Report agreement rate.
- Every eval run writes a JSON report with: git SHA, prompt versions, model config, per-case results, aggregate metrics.
- The CI gate compares aggregates against `ci/thresholds.yaml` and fails the build on regression.
- Prompt **v3 is deliberately flawed** to demonstrate the gate blocking it. Don't "fix" it.

## Safety and guardrails

- Redact PII (phone, email, card numbers, Aadhaar-like IDs) before sending to any provider and before logging.
- Detect prompt injection in user input and in retrieved documents.
- The copilot must never promise refunds, discounts or policy exceptions. Only tool results and policy docs count as authority.
- Escalate to a human for: legal threats, abuse, high-value refunds (threshold in config), repeated failure, or explicit request for an agent.
- Fictional data only. No real customer data anywhere in the repo.

## Environment (developer machine)

- Acer Aspire A515-56G, Windows 11 Home (64-bit), shell: PowerShell (or WSL Ubuntu when I say so)
- CPU: Intel i5-1135G7, 4 cores / 8 threads. GPU: NVIDIA GeForce MX350 (2 GB VRAM) plus Intel Iris Xe. **RAM: 8 GB total**, so memory is the main constraint.
- Give commands that work in PowerShell (`curl.exe`, `$env:NAME`, no Linux-only tools) unless I say I'm in WSL.
- Keep the stack light: one Ollama chat model loaded at a time, small models only (about 1.7B to 4B), short prompts, Docker (WSL2) memory capped in `%UserProfile%\.wslconfig`, and start optional services (Prometheus, Grafana, Phoenix) only when needed via Compose profiles.
- Never assume the GPU is used. Check with `ollama ps` (PROCESSOR column) before quoting speeds.

## Zero-budget rules

- **No paid services.** Never add code that requires a paid API key or a payment method. Free tiers may only be used as optional fallback/judge routes.
- **Model names live only in `gateway/config.yaml`**, assigned by role (`cheap`, `strong`, `judge`, `embed`, `fallback`). Never hard-code a model name elsewhere.
- **Small-model design:** few tools per agent, schema-validated outputs (Pydantic), one retry with the validation error, short prompts, top-k of 3 to 4.
- **Evals are cached and deterministic:** temperature 0, pinned model tags, response cache or record/replay so re-runs are free. Every eval report records git SHA, model tags, quantization and hardware.
- **Two eval tiers:** smoke set (20 to 30 cases, used in CI and dev) and full set (100 to 150, run locally, report committed).
- **CI gate uses replay/deterministic checks** and committed reports. Do not run large models in CI.
- **Load tests target a mock upstream**, not a real model. Real-model latency is measured separately at low concurrency on the developer's machine.
- **"Cost" is shadow pricing**: the published price of a comparable paid model, labelled *estimated equivalent cost* everywhere.
- **RAM is limited:** use Docker Compose profiles (`--profile obs` for Prometheus/Grafana/Phoenix) and keep one model loaded at a time.
- Fictional data only, since free tiers may log inputs.

## Definition of done (per feature)

1. Works end to end through the gateway
2. Has tests (unit, plus integration where it touches services)
3. Emits traces and metrics where relevant
4. Has at least one eval case or metric that covers it
5. `ruff` and tests pass
6. `docs/decisions.md` has an entry if a real design choice was made
7. README or CLAUDE.md commands section updated if something changed

## Out of scope (don't build unless I ask)

- Real payment or real order integrations
- User accounts, login UI, or multi-tenant admin panel
- Fine-tuning
- A from-scratch gateway (we use LiteLLM Proxy; write custom code only where LiteLLM has no equivalent)
- Kubernetes (Docker Compose is enough; a manifest is a stretch goal)

## Current phase

> Update this line as you go.

**Current step: Step 0 (prerequisites).** See `docs/STEP_PLAN.md`. Update this line after each step passes its manual checks.