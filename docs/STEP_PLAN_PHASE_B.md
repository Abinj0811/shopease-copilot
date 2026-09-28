# Phase B — Observability (steps B1 to B15)

Add this to `docs/STEP_PLAN.md` (or keep it as `docs/STEP_PLAN_PHASE_B.md`). Same working rules as Phase A: one file per step, plan mode where marked, run the check yourself, tick the log, commit, then move on.

**Goal:** you can answer "why was this request slow, expensive or wrong?" from a dashboard and a trace.

**Two tools, two jobs:**
- **Prometheus + Grafana** show gateway health over time: request rate, latency, errors, fallbacks, tokens, spend.
- **Phoenix** shows one conversation at a time: the RAG search, each tool call and each model call as a nested trace.

**Your setup, so the dashboards make sense:** `strong` runs on Groq, `cheap` and `embed` run on Ollama, and `strong → cheap` is the fallback.

---

## Footer to append to every prompt

```
Rules:
- Implement ONLY this step. Touch only the file(s) named. Do not create other files, folders, or stubs "for later".
- If this step introduces a value I might want to change (a port, URL, key name, model alias, threshold, count, path), put it in .env, gateway/config.yaml, or config/*.yaml as appropriate — never hard-coded — and add one row for it to docs/CONFIG.md (Name | Purpose | Default | Where set | Example alternative).
- When finished, report: what you created/changed, any new config values (with the CONFIG.md row), and the exact command(s) I should run to check it. Then STOP.
```

## Before B1: keep your machine fast

Ollama already slowed your system, and this phase adds three more containers. All of them go under a Docker Compose **profile** named `obs`, so they only run when you ask:

```bash
docker compose --profile obs up -d     # start observability
docker compose --profile obs down      # stop it (data volumes survive)
```

Postgres, Redis and LiteLLM keep starting normally, without the profile.

---

## Part 1: Metrics (Prometheus + Grafana)

### B1. `gateway/config.yaml`: turn on the Prometheus callback

```
Edit gateway/config.yaml only: enable LiteLLM's prometheus callback under litellm_settings. Read the current LiteLLM docs first and report (a) the exact metrics endpoint path, (b) whether that endpoint requires an Authorization header in this version, and (c) which metrics are available in the open-source version versus enterprise-only. If a metric I would need for request counts, latency, tokens, spend or fallbacks is enterprise-only, report that and stop instead of working around it. Do not add Prometheus or Grafana yet.
[footer]
```

**Check:** restart the gateway, send one request, and read the raw metrics:
```powershell
docker compose up -d litellm
uv run python scripts/check_gateway.py --alias cheap --key $env:COPILOT_GATEWAY_KEY "Say hi"
curl.exe http://localhost:4000/metrics
```
(Add `-H "Authorization: Bearer <master key>"` if Claude Code found that the endpoint needs it.) You should see lines starting with `litellm_proxy_total_requests_metric` and similar. Write down which metric names you see. B2 to B8 depend on them.

Commit: `feat: enable prometheus metrics`

### B2. `observability/prometheus.yml`

```
Create observability/prometheus.yml only: one scrape job for the LiteLLM proxy at its in-network address (litellm:4000 inside the compose network), with scrape_interval taken from a value I can change. If the metrics endpoint needs authentication, handle it in the way LiteLLM's docs recommend for local use and explain the choice. Do not add the Prometheus service yet.
[footer]
```

**Check:** read the file. The target should be the container name, not `localhost`. Inside Docker, `localhost` means the Prometheus container itself.

Commit: `feat: prometheus scrape config`

### B3. `docker-compose.yml`: add `prometheus` (profile `obs`)

```
Add a "prometheus" service to docker-compose.yml only: pinned image tag, profile "obs", mounts observability/prometheus.yml read-only, named volume for data, host port from PROMETHEUS_PORT. Do not touch other services.
[footer]
```

**Check:**
```powershell
docker compose --profile obs up -d prometheus
```
Open `http://localhost:<PROMETHEUS_PORT>/targets` (default 9090). The `litellm` target should say **UP**. Then in the Graph tab run the query `litellm_proxy_total_requests_metric` and confirm data appears.

Commit: `feat: prometheus service`

### B4. `observability/grafana/provisioning/datasources/prometheus.yml`

```
Create observability/grafana/provisioning/datasources/prometheus.yml only: a Grafana datasource named Prometheus pointing at http://prometheus:9090, set as default, so no manual clicking is needed.
[footer]
```

**Check:** read the file. The URL should use the container name `prometheus`.

Commit: `feat: grafana datasource`

### B5. `docker-compose.yml`: add `grafana` (profile `obs`)

```
Add a "grafana" service to docker-compose.yml only: pinned image tag, profile "obs", named volume, host port from GRAFANA_PORT, admin user and password from .env, mounts the observability/grafana/provisioning folder read-only. Do not touch other services.
[footer]
```

Add `GRAFANA_ADMIN_USER` and `GRAFANA_ADMIN_PASSWORD` to `.env` yourself. Pick a real password, not the default.

**Check:**
```powershell
docker compose --profile obs up -d grafana
```
Open `http://localhost:<GRAFANA_PORT>` (default 3000) and log in. Go to **Connections → Data sources**. The Prometheus datasource should already exist. Click **Save & test**; it should say the query succeeded.

Commit: `feat: grafana service`

### B6. Dashboard as code: provider file + first panel

```
Create two files only: observability/grafana/provisioning/dashboards/dashboards.yml (a provider that loads JSON dashboards from a mounted folder) and observability/grafana/dashboards/gateway.json (a dashboard titled "ShopEase Gateway" with ONE panel: requests per minute, split by requested model). Mount the dashboards folder into the grafana service (edit docker-compose.yml only for that mount). Use only metrics I confirmed in B1.
[footer]
```

**Check:**
```powershell
docker compose --profile obs up -d --force-recreate grafana
uv run python scripts/ask.py "What is your return policy for mixers?"
```
Open Grafana → Dashboards → "ShopEase Gateway". The panel should show a bump for the request you just made. Make sure `cheap`, `strong` and `embed` show up as separate series once you've called each.

Commit: `feat: first gateway dashboard panel`

### B7. Add latency panels

```
Edit observability/grafana/dashboards/gateway.json only: add panels for request latency (p50 and p95, by model) and time to first token if available. Use only metrics that exist in my /metrics output.
[footer]
```

**Check:** send a few requests to each alias. Confirm `strong` (Groq) shows lower latency than `cheap` (local), or note the opposite. That comparison is a real result for your README.

Commit: `feat: latency panels`

### B8. Add errors, fallbacks, tokens and spend panels

```
Edit observability/grafana/dashboards/gateway.json only: add panels for failed requests by status code, fallback count, input and output tokens by model, and spend by team. Skip any panel whose metric does not exist and list what you skipped and why.
[footer]
```

**Check, including a forced failure:** set an invalid `GROQ_API_KEY`, restart LiteLLM, send 3 questions through `ask.py`, then restore the key. The fallback and error panels should move. Restore the key and restart LiteLLM afterwards.

Commit: `feat: reliability and cost panels`

---

## Part 2: Traces (Phoenix)

### B9. `docker-compose.yml`: add `phoenix` (profile `obs`)

```
Add a "phoenix" service (Arize Phoenix, self-hosted) to docker-compose.yml only: pinned image tag, profile "obs", named volume for its data, host port from PHOENIX_PORT, and its trace collector port reachable inside the compose network. Use SQLite storage inside the volume, not the existing Postgres. Read the Phoenix docs first for the correct ports and env vars.
[footer]
```

**Check:**
```powershell
docker compose --profile obs up -d phoenix
```
Open `http://localhost:<PHOENIX_PORT>` (default 6006). The Phoenix UI should load with no traces yet.

Commit: `feat: phoenix service`

### B10. `gateway/config.yaml`: send gateway traces to Phoenix

```
Edit gateway/config.yaml only: add the arize_phoenix callback and point it at the self-hosted Phoenix collector inside the compose network, with a project name from config. Read the current LiteLLM Phoenix integration docs first. If the required environment variables must go in docker-compose.yml for the litellm service, tell me exactly which lines instead of editing that file.
[footer]
```

**Check:** apply any compose changes Claude Code named, restart LiteLLM, then send a request. In Phoenix, open the project you named. One trace per gateway call should appear, showing the model, the prompt and the completion.

Commit: `feat: gateway traces to phoenix`

### B11. `copilot/tracing.py`

```
Create copilot/tracing.py only: sets up OpenTelemetry tracing for the copilot, exporting to the Phoenix collector (endpoint and project name from .env or config, sample rate from config/copilot.yaml as trace_sample_rate, default 1.0). Expose a small helper for creating spans. Do not edit copilot/api.py yet.
[footer]
```

**Check:** read the file. Confirm the sample rate and endpoint are configurable, and that if Phoenix is not running, the copilot **still works** and just doesn't export traces. Ask Claude Code how it handles that case.

Commit: `feat: copilot tracing setup`

### B12. `copilot/api.py`: add spans

```
Edit copilot/api.py only: wrap each /chat request in a parent span, with child spans for the RAG search, each tool call (with tool name and arguments), and each model call. Add attributes: conversation_id, prompt_version, agent_name, and whether it escalated. Do not change any behavior of the endpoint.
[footer]
```

**Check:** restart the API, then ask an order question that triggers tools:
```powershell
uv run python scripts/ask.py "My order <delivered order> stopped working, can I return it?"
```
In Phoenix, open the newest trace. It should show one parent span with the RAG search, the tool calls and the model calls nested inside it, each with timing. Confirm the gateway's own trace from B10 lines up with the copilot's model-call spans.

**Failure check:** stop Phoenix (`docker compose --profile obs stop phoenix`), ask one more question, and confirm the copilot still answers normally. Then start Phoenix again.

Commit: `feat: copilot spans`

---

## Part 3: Traffic and proof

### B13. `data/simulated_queries.jsonl`

```
Create data/simulated_queries.jsonl only: 300 realistic customer messages, one JSON object per line with fields id, message, category (policy, order, refund, escalation, out_of_scope, injection). About 30 percent must be near-duplicate paraphrases of other lines (same meaning, different wording), because I will use them later to test caching. Use real order numbers from the seeded data for order and refund messages (read them from the database or from scripts/show_edge_cases.py output). Draft only; I will review it.
[footer]
```

**Check:** read a sample of at least 40 lines yourself. Are the order numbers real? Do the near-duplicates really mean the same thing? Delete or fix anything odd. Count the categories.

Commit: `data: simulated query set`

### B14. `scripts/simulate_traffic.py`

```
Create scripts/simulate_traffic.py only: reads data/simulated_queries.jsonl and sends the messages to the running copilot API. Options: --count, --concurrency (default 1), --delay-ms between requests, --shuffle, --seed. Each message gets its own conversation_id. Print a summary at the end: sent, succeeded, failed, average latency, and how many were served by fallback if that is detectable. Defaults come from config, not code.
[footer]
```

**Check, small first.** Groq's free tier has rate limits, and too much traffic will trip fallback and skew your numbers:
```powershell
docker compose --profile obs up -d
uv run python scripts/simulate_traffic.py --count 20 --concurrency 1 --delay-ms 2000
```
Watch the terminal summary, then the Grafana dashboard, then a couple of traces in Phoenix. If everything looks fine, scale up:
```powershell
uv run python scripts/simulate_traffic.py --count 100 --concurrency 2 --delay-ms 1000
```
If you see many 429s or a jump in fallback, raise `--delay-ms` or lower concurrency. That is a useful finding, so write it in `docs/decisions.md`.

Commit: `feat: traffic simulator`

### B15. Proof: no new code

Run the simulator with a mixed load, then capture evidence:
- Screenshot the Grafana dashboard with live data: save as `docs/images/grafana-gateway.png`.
- Screenshot one full nested trace in Phoenix: save as `docs/images/phoenix-trace.png`.
- In `docs/decisions.md`, write down: p50 and p95 latency for `strong` versus `cheap`, how many requests hit fallback, and how many total tokens the run used.

Commit: `docs: observability evidence`

---

## Config values introduced in Phase B

| Name | Where | Default | Example alternative |
|---|---|---|---|
| `PROMETHEUS_PORT` | `.env` | `9090` | `9091` if taken |
| `GRAFANA_PORT` | `.env` | `3000` | `3001` |
| `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD` | `.env` | your choice | any |
| `PHOENIX_PORT` | `.env` | `6006` | `6007` |
| Phoenix collector endpoint / project name | `.env` or `gateway/config.yaml` | local Phoenix, `shopease` | another project name per experiment |
| Prometheus scrape interval | `observability/prometheus.yml` | `15s` | `5s` for demos |
| `trace_sample_rate` | `config/copilot.yaml` | `1.0` | `0.1` under heavy load |
| Simulator `--count`, `--concurrency`, `--delay-ms` | CLI flags with defaults in config | 20, 1, 2000 | raise cautiously |

## Checkpoint B

- [ ] `docker compose --profile obs up -d` starts everything, and `down` stops it
- [ ] Prometheus target is UP; Grafana dashboard shows requests, latency, errors, fallbacks, tokens and spend
- [ ] Forcing a Groq failure visibly moves the fallback and error panels
- [ ] A Phoenix trace shows RAG, tool calls and model calls nested under one request
- [ ] The copilot still works when Phoenix is down
- [ ] Simulator ran 100+ queries; you have screenshots and numbers in `docs/decisions.md`
- [ ] `docs/CONFIG.md` has a row for every new value
- [ ] You can explain the difference between what Grafana shows and what Phoenix shows

## Step log

| Step | Done | Date | Notes |
|---|---|---|---|
| B1 | | | |
| B2 | | | |
| B3 | | | |
| B4 | | | |
| B5 | | | |
| B6 | | | |
| B7 | | | |
| B8 | | | |
| B9 | | | |
| B10 | | | |
| B11 | | | |
| B12 | | | |
| B13 | | | |
| B14 | | | |
| B15 | | | |

## Known gaps, on purpose

- **Cache hit rate** is not on the dashboard yet. Caching arrives in Phase D, and the near-duplicate queries from B13 are what you'll use to test it.
- **Cost for local models** is shadow pricing, not real money, so label spend panels "estimated equivalent cost".
- **Quality is not measured here.** Dashboards tell you the system is fast and up; Phase C tells you whether the answers are right.