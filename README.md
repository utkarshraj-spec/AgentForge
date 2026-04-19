# AgentForge

> Autonomous Multi-Agent AI Engineering System

AgentForge turns a plain-English prompt into production-ready, secure,
fully tested code without any human developer in the loop. A Senior
Software Agent plans the work, 50–70 specialised sub-agents execute it in
parallel, and a 40–50 round review loop plus an isolated sandbox harden
the result before delivery.

```text
  ┌─────────────┐   ┌──────────────┐   ┌──────────────┐   ┌─────────────┐
  │  Prompt     │→ │ Senior Agent │→ │ Sub-agent    │→ │ Review Loop │
  │  Intake     │   │ (CoT plan)   │   │ pool (||)    │   │ (≤50 rnds)  │
  └─────────────┘   └──────────────┘   └──────────────┘   └─────────────┘
                                                            │
                                                            ▼
                                                     ┌───────────────┐
                                                     │   Sandbox     │
                                                     │  (no net, fs  │
                                                     │   isolated)   │
                                                     └───────────────┘
                                                            │
                                                            ▼
                                                   ┌───────────────────┐
                                                   │ Final Assembler + │
                                                   │ Audit report      │
                                                   └───────────────────┘
```

## Architecture

### Stage 1 — Prompt Intake (`agentforge.intake`)
The user's free-form prompt is parsed into a structured
[`EngineeringSpec`](agentforge/models.py): project name, functional
requirements, non-functional requirements, constraints, target stack, and
success criteria.

### Stage 2 — Senior Agent (`agentforge.orchestrator.senior_agent`)
The master planner. Uses **chain-of-thought reasoning** before assigning
work, then breaks the project into modules (`auth`, `database`, `api`,
`frontend`, `security`, `tests`, `docs`, `infra`, …). Each module is
assigned to a set of sub-agents and placed into a dependency graph that is
executed in parallel waves.

### Stage 3 — Sub-Agent Pool (`agentforge.agents`)
Every agent is a stateless function — all state lives in
[`SharedContext`](agentforge/memory/shared_context.py) (ChromaDB when
available, in-memory fallback otherwise). Agent roles:

| Agent | Role |
|-------|------|
| `code_writer` | Writes module source files |
| `test_writer` | Produces unit + integration tests |
| `security_scanner` | OWASP Top 10 static rules |
| `conflict_detector` | Cross-agent conflicts, route collisions, dangling imports |
| `refactor_agent` | Deterministic auto-fixes for common findings |

### Stage 4 — Review Loop (`agentforge.pipeline.review_loop`)
Runs up to **50 rounds** (configurable via `AGENTFORGE_MAX_REVIEW_ROUNDS`).
Each round performs:

1. **Cross-agent conflict detection** — duplicate files, route collisions, syntax errors.
2. **Security vulnerability scan** — OWASP A01–A10 pattern rules.
3. **Code quality checks** — function length, bare `except`, dead code.
4. **Test coverage verification** — every declared route must appear in a test file.

Findings are auto-patched by `refactor_agent`. The loop exits when zero
critical findings remain (after `AGENTFORGE_MIN_REVIEW_ROUNDS`) or when the
hard ceiling is reached.

### Stage 5 — Sandbox (`agentforge.pipeline.sandbox`)
Materialises the generated code under `<run_dir>/sandbox/workspace` and
runs `pytest` in a subprocess with:

* **Network isolation** via `unshare --net` when available.
* **Filesystem isolation** — writes are refused outside the workspace.
* **Configurable timeout** (default 10 min).

If tests fail, the failing modules are recorded so the Senior Agent can
route them back to sub-agents for revision.

### Stage 6 — Delivery (`agentforge.output`)
Produces:

* `project/` — the final codebase.
* `audit_report.json` — OWASP-tagged security summary.
* `dependency_map.json` — module dependency graph.
* `test_coverage.json` — sandbox results and per-round finding counts.
* `audit_trail.json` — every inter-agent message, timestamped.

## Setup

```bash
# 1. Install (in editable mode, with dev extras).
pip install -e .[dev]

# 2. Copy the env template and fill in keys (or leave them blank to use
#    the deterministic mock provider).
cp .env.example .env
$EDITOR .env
```

### LLM providers

AgentForge supports three providers, selected via `AGENTFORGE_PROVIDER`:

| Value | Requires | Notes |
|-------|----------|-------|
| `openai` | `OPENAI_API_KEY` | Default model `gpt-4o-mini` (override with `OPENAI_MODEL`) |
| `anthropic` | `ANTHROPIC_API_KEY` | Default model `claude-3-5-sonnet-latest` |
| `mock` | — | Deterministic offline provider (CI, tests, demos) |

If you select `openai` or `anthropic` without an API key, AgentForge falls
back to `mock` automatically rather than crashing.

## Running the demo

```bash
agentforge build "Build a REST API for a todo app with user authentication"
```

Output appears under `.agentforge_runs/<timestamp>/`:

```text
.agentforge_runs/20260419T223500/
├── project/                  ← generated, runnable code
│   ├── pyproject.toml
│   ├── user_project/
│   │   ├── auth.py
│   │   ├── database.py
│   │   ├── main.py
│   │   ├── models.py
│   │   ├── schemas.py
│   │   └── security.py
│   └── tests/
│       ├── conftest.py
│       └── test_api.py
├── audit_report.json
├── dependency_map.json
├── test_coverage.json
└── audit_trail.json
```

Skip the sandbox stage (useful when the ambient Python lacks FastAPI):

```bash
agentforge build --skip-sandbox "..."
```

## Tests

```bash
pytest -q
```

## Configuration

All configuration is via environment variables (see
[`.env.example`](.env.example)):

| Variable | Default | Purpose |
|----------|---------|---------|
| `AGENTFORGE_PROVIDER` | `mock` | LLM provider |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | — / `gpt-4o-mini` | OpenAI backend |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | — / `claude-3-5-sonnet-latest` | Anthropic backend |
| `CHROMA_PERSIST_DIR` | `.chroma` | Vector DB persistence root |
| `AGENTFORGE_MAX_REVIEW_ROUNDS` | `50` | Hard ceiling on Stage 4 |
| `AGENTFORGE_MIN_REVIEW_ROUNDS` | `3` | Minimum rounds before exit |
| `AGENTFORGE_LOG_LEVEL` | `INFO` | Python log level |

## Security notes

* The scanner covers OWASP A01–A10 at a minimum.
* The sandbox is isolated by construction (path-traversal-safe) and by
  runtime flags (`unshare --net`). Users requiring stronger isolation
  (Docker, Firejail, gVisor) can wrap the `Sandbox` class directly.
* Agent messages are logged to an append-only audit trail
  (`audit_trail.json`).

## License

MIT
