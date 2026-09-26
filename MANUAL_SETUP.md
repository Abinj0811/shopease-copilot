# Manual Setup — do this yourself before Step A1

This is everything Claude Code will **not** do for you. Work through it top to bottom, in order. Nothing here needs Claude Code.

---

## 1. Install tools

| Tool | Check after install | Notes |
|---|---|---|
| **Git** | `git --version` | |
| **Docker Desktop** (includes Compose) | `docker --version` and `docker compose version` | Start it once after install so the engine is running |
| **uv** (Python package manager) | `uv --version` | Install script is on astral.sh/uv; don't install Python separately, `uv` manages it |
| **Ollama** | `ollama --version` | Download from ollama.com |
| **VS Code** | opens normally | |
| **Claude Code** | see step 5 below | Extension + CLI |

If any command fails, fix that before moving on — everything else depends on these.

---

## 2. Check your machine's specs

You need this to pick model sizes later.

- **Windows:** Task Manager → Performance tab → note total RAM, and check for a dedicated GPU
- **macOS:** Apple menu → About This Mac → note RAM and chip
- **Linux:** `free -h` (RAM) and `nvidia-smi` (GPU, if NVIDIA)

Write down: RAM = ____ GB, GPU = ____ (or "none / CPU only").

---

## 3. Create accounts (no payment method on any of these)

| Account | For | Do now |
|---|---|---|
| **GitHub** | hosting the repo, CI | create if you don't have one |
| **Groq** (console.groq.com) | free-tier fallback/judge model | sign up, generate an API key, **do not add a card** |

Optional, only if you plan to use it later for trace inspection:
- **None needed right now** — Phoenix runs self-hosted in Docker, no account required.

---

## 4. Create the GitHub repository

1. On GitHub: New repository → name it `shopease-copilot` → Public → add an MIT license → do **not** add a README, .gitignore, or any other file (Claude Code will create those in Step A1/A2).
2. Clone it locally:
   ```bash
   git clone https://github.com/<your-username>/shopease-copilot.git
   cd shopease-copilot
   ```

---

## 5. Install and sign in to Claude Code

1. In VS Code: Extensions → search "Claude Code" → Install.
2. Also install the CLI (needed for some features): follow the install link in the extension's welcome panel, or run the installer from claude.com/code.
3. Open the `shopease-copilot` folder in VS Code.
4. Open the Claude Code panel (sidebar icon) and sign in.
5. Confirm it works: type a one-line question in the panel and get a reply.

---

## 6. Pull your local models

Based on your RAM/GPU from step 2, pick model sizes:

| Your machine | `cheap` role | `strong` role | `embed` role |
|---|---|---|---|
| 8 GB RAM, CPU only | small, ~3B, quantized | small, ~3-4B, quantized | a small embedding model |
| 16 GB RAM | ~3-4B | ~8-12B, quantized | a small embedding model |
| 32 GB RAM or 12-24 GB GPU | ~4-7B | ~14-30B | a small embedding model |

Check the Ollama library (ollama.com/library) for current model names and sizes — they change often, so don't rely on a name from an older conversation.

```bash
ollama pull <cheap-model>
ollama pull <strong-model>
ollama pull <embed-model>
```

Confirm:
```bash
ollama list
ollama run <cheap-model> "Say hello in one line"
```

Write your three chosen model names somewhere handy — you'll give them to Claude Code in Step A10.

---

## 7. Place the planning docs in the repo

Copy these three files (already written) into the fresh repo:

```
shopease-copilot/
├── CLAUDE.md
└── docs/
    ├── STEP_PLAN.md
    └── ARCHITECTURE.md
```

Commit them:
```bash
git add .
git commit -m "docs: add project plan and architecture"
git push
```

---

## 8. What the repo looks like right now

This is the **entire** structure before Step A1. Everything else appears one file at a time as you work through `docs/STEP_PLAN.md` — do not create empty folders in advance.

```
shopease-copilot/
├── CLAUDE.md
└── docs/
    ├── STEP_PLAN.md
    └── ARCHITECTURE.md
```

### Target end state (for reference only — do not create these now)

This is where the repo will end up after Phase A. You are **not** creating this now; it's here so you know what each future file is for. `docs/CONFIG.md` will fill in as you go and is the real source of truth for every value.

```
shopease-copilot/
├── CLAUDE.md
├── README.md                      (A1 area)
├── pyproject.toml                 (A1)
├── .gitignore                     (A2)
├── .env.example                   (A2)
├── .env                           (you create locally, never committed)
├── docker-compose.yml             (A6, extended in A11)
├── .github/
│   └── workflows/ci.yml           (A5)
├── common/
│   └── settings.py                (A8)
├── gateway/
│   ├── config.yaml                (A10, A13, A38)
│   └── teams.yaml                 (A14)
├── config/
│   ├── business_rules.yaml        (A17, you write the values)
│   ├── seed.yaml                  (A19)
│   ├── rag.yaml                   (A25)
│   └── copilot.yaml               (A31)
├── db/
│   └── (Alembic env + migrations) (A18, A26, A35)
├── data/
│   ├── seed_db.py                 (A20)
│   └── policies/
│       ├── *.md                   (A23)
│       └── TRAPS.md                (A23)
├── copilot/
│   ├── api.py                     (A33, A36)
│   ├── rag/                       (A26, A27)
│   ├── tools/                     (A28, A29, A30)
│   └── prompts/
│       └── support_agent/v1.md    (A32)
├── ui/
│   └── app.py                     (A37)
├── scripts/
│   ├── check_infra.py             (A7)
│   ├── check_ollama.py            (A9)
│   ├── check_gateway.py           (A12)
│   ├── create_keys.py             (A15)
│   ├── show_spend.py              (A16)
│   ├── check_rules.py             (A17)
│   ├── show_edge_cases.py         (A21)
│   ├── check_policies.py          (A24)
│   ├── ask.py                     (A34)
│   └── check_fallback.py          (A39)
├── tests/
│   └── test_smoke.py              (A4, grows in A30)
└── docs/
    ├── STEP_PLAN.md
    ├── ARCHITECTURE.md
    ├── CONFIG.md                  (A3, updated every step)
    └── decisions.md               (you write into this throughout)
```

---

## 9. The `.env` file

You create this file yourself; it is never committed. Two ways to handle it:

1. **Recommended:** after Claude Code creates `.env.example` in Step A2, run `cp .env.example .env`, and from then on, every time a step's check tells you to add a value, open `.env` and add it by hand. `.env.example` stays as documentation (with blank/placeholder values); `.env` holds your real local values.
2. Never paste real API keys or generated virtual keys into a Claude Code prompt — type them into `.env` yourself.

Track what's in it against `docs/CONFIG.md`, which Claude Code updates every step with a row per value (name, purpose, default, where it's set, an example alternative).

---

## 10. Ready check

Before starting Step A1, confirm:

- [ ] `git --version`, `docker --version`, `docker compose version`, `uv --version`, `ollama --version` all work
- [ ] Docker Desktop is running
- [ ] Your RAM/GPU is noted
- [ ] GitHub repo created, cloned, and Claude Code opens it
- [ ] Groq account created, API key generated, no card attached
- [ ] `cheap`, `strong`, `embed` models pulled and each replies to a test prompt
- [ ] `CLAUDE.md`, `docs/STEP_PLAN.md`, `docs/ARCHITECTURE.md` committed and pushed
- [ ] Repo contains nothing else yet

When every box is checked, open `docs/STEP_PLAN.md` and start **A1**.