# Conductor

A local model router: one Anthropic-compatible endpoint that any harness can
point at. Policy decides which model actually serves each request; every call
lands in a SQLite ledger; a report command finds routing mistakes after the fact.

## Install & run

No clone needed — run straight from GitHub with [uv](https://docs.astral.sh/uv/):

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export OPENROUTER_API_KEY=sk-or-...   # optional, for non-Claude models
uvx --from git+https://github.com/davidlifschitz/conductor conductor-proxy
```

First run creates `~/.conductor/` with a default `policy.yaml` and
`pricing.yaml` — edit those to change routing and prices. Or install the
commands permanently:

```bash
uv tool install git+https://github.com/davidlifschitz/conductor
conductor-proxy                # the router, on :8484
conductor-dashboard            # live terminal UI
conductor-report --days 7      # retro analysis
```

(`pip install git+https://github.com/davidlifschitz/conductor` works too.)

### From a clone

```bash
git clone https://github.com/davidlifschitz/conductor && cd conductor
uv sync
uv run conductor-proxy         # or: uv run uvicorn conductor.proxy:app --port 8484
```

## Point a harness at it

```bash
export ANTHROPIC_BASE_URL=http://localhost:8484
claude          # Claude Code now routes through Conductor
```

Anything that speaks the Anthropic Messages API works the same way.

## Steering routes

- Header: `x-conductor-tag: plan` forces the frontier model; `cheap` forces the driver.
- In-band: start a system prompt with `[conductor:plan]`.
- Otherwise `policy.yaml` rules fire top-to-bottom: planning language → frontier,
  huge context → mid-tier, everything else → daily driver.

## Retro analysis

```bash
conductor-report --days 7      # or: python -m conductor.report --days 7
```

Prints spend by model and rule, plus:
- **downgrade candidates** — frontier calls with small in/out and no explicit
  tag (probably should have been the driver)
- **escalation candidates** — cheap-model calls retried near-identically
  within 3 minutes (the first answer probably wasn't good enough)

Fill in `pricing.yaml` first; unknown prices are reported as `?` rather than
guessed.

## v0.2 features (built via parallel agent dispatch — see AGENTS.md)

- **Auto-escalation** (`escalate.py`): non-streaming responses that look weak
  (empty content, refusal, instant truncation, high uncertainty density) are
  transparently retried one rung up the `escalation.ladder`. Both calls are
  logged; the retry row carries rule `escalated:<reason>`.
- **Context portability** (`context_layer.py`): drop `SKILL.md` and
  `REPORT.md` in `~/.conductor/context/` and Conductor prepends them to every
  request's system prompt — the same memory rides along across every harness
  and model. Opt out per request with `[conductor:nocontext]`.
- **OpenAI-compatible ingress** (`openai_compat.py`): `/v1/chat/completions`
  with streaming translation, so non-Anthropic harnesses route through the
  same policy and ledger. Tool-call translation not yet covered.

## Dashboard

```bash
conductor-dashboard              # or: python -m conductor.dashboard
```

Full-screen live view: proxy health, spend by model/rule, and a real-time
tail of every request (escalations highlighted). Keys: q quit, p pause,
e escalations-only. One-shot variants:

```bash
conductor-dashboard stats --days 7
conductor-dashboard tail -n 50 --follow
conductor-dashboard show 212
```

Read-only over `conductor.db` and `GET /health` — safe to run anytime, even
while the proxy is down. Set `CONDUCTOR_HOME` (e.g. `~/.conductor` when
installed via uvx/uv tool) so the dashboard and report find the ledger;
`--db` overrides per invocation.

## Web dashboard

```bash
conductor-dashboard web              # browser UI on http://127.0.0.1:8485
conductor-dashboard --web            # same, flag on default live parser
conductor-dashboard web --port 9000
```

Browser-based alternative to the terminal UI. **Live**, **Stats**, and **Tail**
tabs mirror the TUI: ledger polling for new requests, summary/health refresh,
and row-detail modals. The **Agents** tab adds a launcher for coding agents
(Claude Code, Codex CLI, Cursor, and others) plus an MCP integrations panel
(mock UI for now). Read-only over `conductor.db` — no policy edits or ledger
writes. Design reference and screenshots live in `docs/design_handoff/`.

## Development

```bash
uv sync --all-groups     # env with dev tools
uv run pytest            # test suite
uv run ruff check .      # lint
uv run ruff format .     # format
```

## Still open

- Escalation for streaming responses (currently non-streaming only — you
  can't un-stream a weak answer the client already saw).
