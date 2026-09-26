# Architecture — ShopEase Copilot (zero-budget edition)

Put this file in `docs/ARCHITECTURE.md`. It is the source of truth for tooling and model choices.

## 1. Principles

1. **Local-first.** Everything essential runs on your machine with open-weight models via Ollama. No paid API is required to build, test or demo the project.
2. **Free hosted APIs are optional extras**, used only as a fallback route and as a judge. They throttle, change and disappear, so nothing critical depends on them.
3. **Every tool is open source and self-hosted** (LiteLLM, Postgres, Redis, Prometheus, Grafana, Phoenix or Langfuse, Streamlit).
4. **Deterministic and cached evals.** Temperature 0, pinned model tags, and a response cache so re-running an eval costs nothing and CI is repeatable.
5. **Honest numbers.** Report results for the hardware you actually have. A slow CPU-only latency figure with a clear caveat beats an invented one.
6. **Model names live in one config file** (`gateway/config.yaml`). Model catalogs and free tiers change monthly.

## 2. Component choices

| Layer | Tool | Why | Cost |
|---|---|---|---|
| Model runtime | **Ollama** | One-command pulls, OpenAI-compatible endpoint, runs on CPU or GPU | Free |
| Gateway | **LiteLLM Proxy** | Keys, budgets, rate limits, fallbacks, cache, callbacks | Free (check enterprise-only features) |
| App | FastAPI + Pydantic v2 | Standard, typed | Free |
| Agents | LangGraph (phase 2) or hand-rolled loop | Supervisor pattern | Free |
| Vector store | **pgvector** in Postgres | One less service | Free |
| Cache / limits | Redis | LiteLLM cache and rate limiting | Free |
| Embeddings | Local model via Ollama or `sentence-transformers` (multilingual option: BGE-M3) | No API needed | Free |
| Reranker (optional) | Small BGE reranker via `sentence-transformers` on CPU | Retrieval quality boost | Free |
| Metrics | **Prometheus + Grafana** | Gateway health dashboards | Free |
| LLM traces | **Arize Phoenix** (lightweight, single container) or **Langfuse** (heavier, more features) | Trace inspection, nested agent spans | Free, self-hosted |
| PII guardrail | Microsoft Presidio | Local, no API | Free |
| Evaluation | Custom runner + LLM-as-judge; DeepEval/RAGAS/promptfoo only where useful | Full control, JSON reports for CI | Free |
| UI | Streamlit | Thin and quick | Free |
| Load test | Locust | Scriptable | Free |
| CI | GitHub Actions (public repo) | Lint, tests, eval gate | Free |

Verify current licensing and feature availability for LiteLLM (Prometheus, semantic cache, guardrails) in its docs before depending on them. If a feature is paid-only, write the gap as custom code or drop it, and note it in `docs/decisions.md`.

## 3. Model roles

Assign models by **role**, not by brand. Roles are stable; specific models are not.

| Role | Used by | What to pick | Notes |
|---|---|---|---|
| `cheap` | Triage, intent classification, simple FAQ | Small model, about 1.7B to 4B parameters | Must follow JSON/tool schemas reliably |
| `strong` | Refunds, tool-heavy and policy-edge cases, reviewer agent | Mid-size model, about 8B to 30B depending on hardware | Must support native tool calling in Ollama |
| `judge` | Evaluation only | A model from a **different family** than the generator, as large as you can run | Reduces self-preference bias; calibrate against your own labels |
| `embed` | RAG indexing and retrieval, semantic cache | Small embedding model | Re-index when you change it |
| `fallback` | Gateway fallback route | One free hosted model | Unreliable by nature; treat 429s as normal |

### Hardware tiers (pick the one that matches your machine)

| Your machine | Suggested setup | Expect |
|---|---|---|
| **8 GB RAM, CPU only** | `cheap` and `strong` both small (about 3B to 4B, quantized). Run the judge on a free hosted model | Slow but workable. Keep golden-set runs small |
| **16 GB RAM** | `cheap` about 3B to 4B; `strong` about 8B to 12B, quantized. One model loaded at a time | Good default for this project |
| **32 GB RAM or a 12 to 24 GB GPU** | `strong` in the 14B to 30B class; judge can be local | Best quality; can run the judge locally overnight |

Model families that were commonly recommended for local use at the time of writing include Qwen3 (Apache 2.0, sizes from 1.7B up), Gemma 4, Phi-4, Mistral Small and gpt-oss-20b. Check the Ollama library for what is available today and confirm each license.

Tips for small machines:
- Set `OLLAMA_MAX_LOADED_MODELS=1` so models swap instead of exhausting RAM.
- Use quantized builds (Q4 class) and short contexts.
- Close browser tabs during eval runs.

## 4. Free hosted APIs: how to use them safely

Use one or two free tiers (Google AI Studio, Groq, OpenRouter free models, Mistral, Cerebras, and others come up in current lists). Rules:

- **Never let the copilot's happy path depend on them.**
- **They are the fallback route**, which conveniently makes the fallback demo real: stop Ollama and traffic shifts to the hosted route.
- **They are the judge route** for evals, throttled and cached.
- **Expect churn.** Providers retire models and change limits without notice, so keep model names only in `gateway/config.yaml`.
- **No payment method attached anywhere.** Free tiers throttle instead of billing.
- **Fictional data only**, since free tiers may log or train on inputs. Read each provider's data policy.
- Check each provider's current published limits before designing around them.

## 5. Small-model reliability: design for it

Small local models are weaker at tool calling and instruction following. This is a feature of the project, not a blocker:

- **Prefer few tools per agent.** The multi-agent phase helps here: each specialist has 1 to 3 tools instead of one agent juggling all of them.
- **Constrain outputs** with JSON schema / structured outputs where supported, and validate with Pydantic. Retry once with the validation error.
- **Add a tiny tool-calling benchmark** (about 20 cases) that you run whenever you change the model, before the full eval.
- **Shorten prompts** and keep retrieved context tight (top-k of 3 to 4, rerank if needed).
- **Measure it.** "Tool-call accuracy by model size" is a real result for your README.

## 6. Evaluation at $0

- **Cache everything.** Enable LiteLLM caching for eval traffic (or add a simple record/replay layer keyed by prompt hash + model + params). A repeated run is free and identical.
- **Two-tier runs:**
  - *Smoke set* (20 to 30 cases): runs in minutes, used during development and in CI.
  - *Full set* (100 to 150 cases): runs locally, sometimes overnight. Commit the resulting JSON report under `evals/reports/`.
- **Judge design:** rubric with examples, reference-based scoring where an expected answer exists, and a different model family from the generator. Hand-label 30 to 50 answers and report judge-human agreement.
- **Golden set authoring:** draft with Claude Code or a local model, then review every case yourself. Nothing is auto-accepted.
- **Report per role:** quality by model size, cost estimates (see below), latency on your hardware.

### "Cost" when everything is free

Local inference costs no money, but the cost tracking story still matters. Do this:
- Keep **shadow pricing** in `gateway/config.yaml`: the published per-token price of a comparable paid model. The dashboard then shows "what this would cost on a paid API" and "savings from routing and caching."
- Label it clearly as *estimated equivalent cost* in dashboards and the README.
- Also track real resource cost: tokens per second, wall-clock time, RAM.

## 7. CI eval gate at $0

GitHub-hosted runners are CPU-only and small, so the gate must be lightweight:

- **Default: replay mode.** CI runs the smoke set against a **recorded response cache** committed to the repo (or stored as a CI artifact), and grades with deterministic checks: tool-call accuracy, retrieval recall@k, escalation recall, policy-violation regex/rule checks, and guardrail tests. These need no LLM.
- **When a prompt or config changes,** the cache misses. Options: run a tiny model (about 1B to 3B) in CI on the smoke set, or require the developer to run `make eval-smoke` locally and commit the updated report, which CI then verifies.
- **Full LLM-judge scores are computed locally**, not in CI. CI checks that the committed report is fresh (matches the git SHA of the prompt files) and meets thresholds.
- **Demonstrate the gate:** prompt v3 is deliberately flawed. Its committed report fails the thresholds, and CI blocks the PR. Screenshot it.

## 8. Observability choices

- **Gateway metrics:** LiteLLM's Prometheus metrics into Grafana (latency, errors, fallbacks, cache hit rate, token counts). Add custom metrics for gaps.
- **Trace UI:**
  - **Phoenix** is a single container and lighter on RAM. Recommended for 8 to 16 GB machines.
  - **Langfuse** self-hosted needs more services (verify current requirements) and more RAM, but has richer prompt management and evals. Use it if you have 32 GB or want those features.
  - Both attach through LiteLLM callbacks and OpenTelemetry. Pick one; don't run both.
- **Trace sampling:** 100% in dev is fine locally, since there's no per-trace fee.

## 9. Load testing without a GPU

Load-testing a CPU-hosted model just measures the CPU. Instead:

- **Test the gateway, not the model.** Point a `mock` alias at a fake upstream (LiteLLM supports mock responses, or run a tiny stub server that returns canned OpenAI-format replies with configurable delay).
- Report requests/sec, p95 latency and gateway overhead at 10, 50 and 100 concurrent users, with cache on and off.
- Separately, report **real model latency** at concurrency 1 to 2 on your own hardware, labelled with the machine specs.

## 10. Resource budget (rough planning numbers)

Estimates only; measure on your machine.

| Piece | Approx RAM |
|---|---|
| Postgres + Redis | 0.5 GB |
| LiteLLM Proxy | 0.5 to 1 GB |
| Prometheus + Grafana | 0.5 to 1 GB |
| Phoenix | 0.5 to 1 GB |
| Streamlit + copilot | 0.5 GB |
| **Platform total** | **about 3 to 4 GB** |
| Model(s) | Sized by tier above; a quantized 8B to 12B model is typically several GB |

On 8 GB machines, run only the models and the copilot plus gateway during evals, and start observability services only when you need dashboards. Use Docker Compose profiles (`--profile obs`).

## 11. Free demo and deployment

- **Primary deliverable:** a reproducible `docker compose up` with a README quickstart, plus screenshots and a 3 to 5 minute demo video.
- **Optional live demo:** a lightweight UI-only demo on a free host (for example Hugging Face Spaces) that calls a free hosted model. Don't promise uptime.
- Don't host the full stack publicly. It isn't needed for a portfolio and exposes free-tier keys to abuse.

## 12. Decisions to record in `docs/decisions.md`

- Why LiteLLM Proxy instead of a custom gateway, and where custom code was needed
- Why local-first, and the fallback design
- Which models filled which roles, and why (with your benchmark numbers)
- Phoenix vs Langfuse choice
- Replay-based CI gate design and its limits
- Judge model choice and calibration results

## 13. Risks specific to the zero-budget setup

| Risk | Mitigation |
|---|---|
| Machine too slow for evals | Smoke set first, cache, overnight full runs, judge on a free hosted model |
| Small model tool-calling failures | Few tools per agent, schema validation, tool benchmark, honest reporting |
| Free tier disappears or throttles | Local-first, fallback chain, model names in config, cached judge results |
| LiteLLM feature is paid-only | Check docs early (Prompt 4), plan custom code or descope |
| RAM exhaustion | One loaded model, Compose profiles, quantized models |
| Results not comparable across machines | Record hardware, model tags and quantization in every eval report |