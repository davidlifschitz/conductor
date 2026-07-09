# Handoff: Conductor GUI (web dashboard + agent launcher)

## Overview

Conductor is a local LLM router/proxy (FastAPI) with a read-only ledger (SQLite) and, today, a terminal UI (`rich`) for monitoring it — a live tail of requests, spend-by-model/rule summaries, and one-shot `stats`/`tail`/`show` subcommands (see `conductor/dashboard/` in the main repo).

This handoff replaces/extends that terminal UI with a **graphical dashboard**, plus a new capability that doesn't exist in the CLI today: an **Agents** tab for launching coding agents (Claude Code, Codex CLI, Cursor, OpenCode, T3 Chat, OpenClaw, Hermes) in either CLI or App mode, and a **plugins/integrations (MCP)** panel (Google Drive, GitHub, Linear, Plaid, plus custom MCP servers).

## About the design files

The bundled file (`Conductor GUI (design reference).dc.html`) is a **design reference built in HTML** — an interactive prototype showing intended look, layout, and behavior. It is **not production code to copy directly**. The task is to recreate this design inside Conductor's actual runtime using patterns that fit a local Python CLI tool:

- Conductor already ships a FastAPI app (`conductor/proxy.py`) and a read-only data layer (`conductor/dashboard/data.py`) that queries the SQLite ledger and calls `GET /health`. The natural implementation is a **new FastAPI route (e.g. `conductor-dashboard --web`, serving on its own port) that serves this UI and exposes the existing `data.py` queries as JSON endpoints**, with the frontend polling them the same way `app.py`'s `LiveDashboard.tick()` does today (new-rows-since-cursor, summary refreshed every few ticks).
- No frontend framework exists in the repo yet — pick the simplest thing that fits a single-binary Python tool shipped via `uvx`/`pip` (e.g. a static HTML/JS bundle with no build step, or a small React/Vite build committed as static assets). Do not introduce a heavy SPA toolchain unless the team already wants one.
- The **Agents launcher** and **MCP/integrations panel** are new product surface with no backend today — see "Open questions" below.

## Fidelity

**High-fidelity.** Colors, type, spacing, and copy in the design are final; recreate them pixel-for-pixel in whatever stack is chosen. Layout is inline-styled in the source (a constraint of the design tool used, not a recommendation) — translate it to real CSS/whatever styling approach the chosen stack uses.

## Screens / views

The design has four tabs inside one dashboard shell (dark app window, 1240×840 in the mock — treat as a min-width desktop layout, not a fixed canvas).

### Shell (present on every tab)
- Top nav bar, 46px tall, background `#0d1117`, 1px bottom border `#1c2128`.
  - Wordmark "CONDUCTOR", 13px/700, monospace, `#e6edf3`.
  - Status dot: 7px circle, `#3fb950`, `box-shadow: 0 0 8px #3fb950` (glow) when proxy is up; red `#f85149` with no glow and label "DOWN (‹reason›)" when down (mirrors `render.header_bar` in the CLI).
  - "UP" label 11px monospace `#3fb950`.
  - Ladder text: `ladder: haiku-4-5 → sonnet-4-6 → fable-5` — pulled from `policy.yaml`'s `escalation.ladder` (same as `data.load_ladder`), each model name stripped of a `claude-` prefix.
  - Right-aligned nav items: LIVE / STATS / TAIL / AGENTS. Active tab: text `#e6edf3` on `rgba(255,255,255,.06)` pill; inactive: `#8b949e` on transparent. 11px, 6px/12px padding, 5px radius.
- KPI strip directly under the nav: 4 equal tiles, 1px gaps of `#1c2128` between them (creates hairline dividers), each tile background `#0d1117`, padding `12px 18px`:
  - TOTAL CALLS (label `#8b949e` 10px letter-spacing .05em; value 24px/700 monospace `#e6edf3`)
  - SPEND (value `#58a6ff`, `text-shadow: 0 0 12px rgba(88,166,255,.5)`)
  - ESCALATIONS (value `#e3b341`)
  - ERRORS (value `#f85149`)
  - These four map directly to `Summary.total_calls`, `Summary.total_cost` (formatted `$0.0000` or `?` if all-null), `Summary.escalation_count`, `Summary.error_count`.

### Live tab
- Two side-by-side boxed panels ("by model", "by rule"), 1px border `#1c2128`, 6px radius, background `#0d1117`, header label in `#58a6ff` 10px monospace styled like a terminal panel title (`┌─ by model ─`). Each row: name left, `calls · cost` right in `#8b949e`.
- Below, a full-width request table in the same boxed style, sticky header row, columns: `id · time · harness · rule · req → routed · tok · cost · ms`.
  - **Model-transition cell** (the one deliberately-designed element): not plain text — two small pill chips joined by a `➜` glyph. Left chip = short requested-model name (`#8b949e` text, `#161b22` bg, `#21262d` border). Right chip = routed model, highlighted (`#c9e8ff` text, `rgba(88,166,255,.1)` bg, `rgba(88,166,255,.35)` border) since that's what Conductor actually served.
  - Row text color: normal `#c9d1d9`; escalated rows (`rule` starts with `escalated:`) render `#e3b341` with a leading `⤴` mark; error rows (`status != 200`) render `#f85149`. Directly mirrors `render.row_style` / `render.tail_table` in the CLI.
  - Clicking a row opens a centered modal (see "Row detail" below).

### Stats tab
- Range control: 24H / 7D / 30D pills (active: `#e6edf3` on `rgba(255,255,255,.08)`; inactive `#8b949e`). In the mock this only toggles the active-pill styling and the range label — a real implementation should re-run `fetch_summary(db, since_ts)` with the corresponding window (mirrors CLI `--days`).
- Spend-trend sparkline: inline SVG line chart (filled area under the line at 8% opacity `#58a6ff`) — placeholder data; real implementation needs a new time-bucketed query (not present in `data.py` today — see Open questions).
- "By model" panel: each row shows model name, call count, a small horizontal bar (`#58a6ff`, glow shadow) scaled to that model's share of max spend, and cost.
- "By rule" panel: plain rule/calls/cost rows, same as the CLI's `summary_panels`.

### Tail tab
- Two native `<select>` filters (model, rule; "all" as the default/reset option) that filter the row list client-side.
- Plain list rows (no table grid) — same fields and same model-transition chip treatment as Live.

### Agents tab (new capability, no CLI equivalent)
- Intro line: "every session below is proxied through conductor — same policy, same ledger, whichever agent you launch."
- Grid of agent cards, 4 per row, gap 14px. Cards for: **Claude Code, Codex CLI, Cursor, OpenCode, T3 Chat, OpenClaw, Hermes**. Each card (`#0d1117` bg, `#1c2128` border, 8px radius, 14px padding):
  - 30×30px monogram avatar, 7px radius, solid accent color, 2-letter code, `#0a0d12` text.
  - Agent name, 12.5px/600, `#e6edf3`.
  - CLI/APP segmented toggle (2px padding container `#12161d`/`#1c2128` border; active segment `#1c2128` bg + `#e6edf3` text, inactive `#6e7681` text on transparent).
  - A "target" line showing what will actually be opened: for CLI mode, `$ cd ‹project dir›`; for App mode, the deep link/URL host. 10px monospace `#6e7681`, truncated with ellipsis.
  - Primary action button (full-width accent-colored pill, `#0a0d12` text, 600 weight): label is **"Copy launch command"** in CLI mode or **"Open app ↗"** in App mode. On click: CLI mode copies `cd ‹dir› && ‹cli command›` to the clipboard; App mode does `window.open(‹deep link›)`. Button label flips to "Copied ✓" for ~1.6s as confirmation.
  - Secondary "Preview session" button (outlined, `#8b949e` text) opens a **simulated** session modal — see below. This is explicitly a mock/preview, distinct from the primary action which is meant to actually hand off to the real local app/CLI.
- Below the agent grid: **"plugins & integrations (MCP)"** section, same 4-per-row grid:
  - Integration cards (Google Drive, GitHub, Linear, Plaid): avatar, name, a connection-status dot+label (`#3fb950`/"Connected" or `#6e7681`/"Not connected"), and a Connect/Disconnect toggle button.
  - Any custom MCPs the user has added render as additional cards (name + server URL, truncated monospace, with a red "Remove" action).
  - A dashed-border "+ Add custom MCP" card opens a small centered form modal (name + server URL inputs, "Add MCP" submit button, `#58a6ff` accent) that appends a new custom-MCP card.

### Row detail modal
- Centered overlay, dark scrim `rgba(0,0,0,.55)` behind, card 400px wide, `#0d1117` bg, `#2a323c` border, 10px radius, blue ambient glow shadow.
- Header: "REQUEST #‹id›" + a "✕" close (also closes on scrim click).
- Body: monospace label/value lines — time, harness, rule, `requested → routed` models, stream (yes/no), status, latency, tokens (`in/out`), cost. Mirrors `render.detail_text` / the CLI's `show <id>` command field-for-field.

### Agent session modal (Preview)
- Centered overlay, 820×560 card, header bar with agent avatar/name, a mode label ("— CLI session" / "— App session"), and a green pill "routed via conductor → ‹model›" tag — reinforcing that even an agent's own session still goes through Conductor's routing/ledger.
- **CLI mode body**: black-ish terminal panel (`#080a0d`), monospace lines simulating an agent run (export env var, invoke command, a routing confirmation line in `#3fb950`, a mock diff with `#58a6ff`/`#f85149` +/- lines, a green "tests passing" line, and a blinking cursor block using a `pulse` opacity keyframe, 1s infinite).
- **App mode body**: chat transcript — right-aligned user bubble (`#1f6feb` fill, white text, 10/10/2/10px radii), left-aligned assistant bubble (`#161b22` fill) preceded by a small green "‹rule› → ‹routed model›" routing tag, with an inline code/diff block and a bottom message composer (placeholder input + accent "Send" button).

## Interactions & behavior

- **Tab switching** (Live/Stats/Tail/Agents): simple state toggle, no animation in the mock — add a subtle crossfade if desired but it's not required.
- **Row click** → opens Row detail modal; click scrim or ✕ to close.
- **Range pills** (Stats tab): toggle active state; wire to re-query `fetch_summary` with the matching `since_ts`.
- **Model/Rule filter selects** (Tail tab): client-side filter of the already-fetched row buffer; "all" resets.
- **Agent CLI/App toggle**: per-card local state, persists which mode is selected.
- **Agent primary action**: clipboard copy (CLI) or `window.open` (App) + transient "Copied ✓" confirmation state (~1.6s timeout).
- **Preview session button**: opens the Agent session modal in the mode currently selected on that card.
- **Integration Connect/Disconnect**: toggles a boolean per integration (mock only — no real OAuth flow implemented).
- **Add custom MCP**: opens a form modal; submit requires both name and URL non-empty, appends to a list, closes the modal.
- **No animations/transitions beyond**: modal appears instantly (add a fade/scale-in in production if desired), and the CLI-mode terminal cursor blink (`opacity 1 → 0.35 → 1`, 1s, infinite, ease default).

## State management

Minimum state needed per screen:
- `activeTab`: `'live' | 'stats' | 'tail' | 'agents'`
- `statsRange`: `'24h' | '7d' | '30d'`
- `tailModelFilter`, `tailRuleFilter`: string, `'all'` default
- `selectedRequestId`: id or null (drives the Row detail modal)
- `agentModes`: map of agentId → `'cli' | 'app'`
- `openSession`: `{ agentId, mode } | null` (Preview modal)
- `copiedAgentId`: id or null, cleared after ~1.6s (button confirmation)
- `integrations`: map of integrationId → boolean connected
- `customMcps`: list of `{ id, name, url }`
- `mcpFormOpen`, plus the two draft input values while the Add-MCP form is open

Data fetching (real, not mock):
- Reuse `conductor/dashboard/data.py` as-is: `fetch_new_rows` (tail cursor), `fetch_recent_rows` (initial backfill), `fetch_summary(db, since_ts)`, `fetch_health(proxy_url)`, `load_ladder(policy_path)`. Expose each as a small JSON endpoint on a new FastAPI router, and poll from the frontend the same way `LiveDashboard.tick()` does (tail every ~1s, summary/health every ~5th tick).
- The Stats sparkline needs a **new** time-bucketed spend query (e.g. `SUM(cost_usd)` grouped by hour/day over the window) — this doesn't exist in `data.py` yet.

## Design tokens

**Palette** (dark, terminal-derived):
- Background base: `#0a0d12` / `#090c10` / `#0d1117` (app shell vs. panel vs. nav — pick one canonical background and one canonical panel color; the mock uses several interchangeably for depth)
- Panel/card background: `#0d1117`, `#12161d`
- Borders/dividers: `#1c2128`, `#21262d`, `#2a323c`
- Text: primary `#e6edf3`, secondary `#c9d1d9`, tertiary/dim `#8b949e`, quaternary `#6e7681`
- Accent — info/routed: `#58a6ff`
- Accent — success/up: `#3fb950`
- Accent — warning/escalated: `#e3b341`
- Accent — error: `#f85149`
- Agent avatar accents (one per agent, decorative): Claude Code `#d97757`, Codex `#79c0ff`, Cursor `#a371f7`, OpenCode `#3fb950`, T3 Chat `#e3b341`, OpenClaw `#f47067`, Hermes `#56d4dd`

**Typography:**
- UI chrome: **Inter** (400/500/600/700)
- Data, code, terminal content: **JetBrains Mono** (400–700)
- Sizes used: 10px (labels/meta) · 10.5–11px (table/body data) · 12–13px (card titles, chat) · 15px (screen title) · 19–24px (KPI values)

**Radii:** 4–5px (chips, buttons) · 6–8px (cards, panels) · 10px (modals, outer shell)

**Shadows/glow:** `0 8px 30px rgba(0,0,0,.35)` (outer shell) · `0 0 8px ‹accent›` (status dot) · `0 0 12px rgba(88,166,255,.5)` (spend text-shadow) · `0 0 40–50px rgba(0,0,0,.4–.5)` (modals)

## Assets

No image/icon assets — all avatars are 2–3 letter monogram badges in solid accent colors (no external icons or logos used, by design, to avoid depending on third-party brand marks).

## Files

- `Conductor GUI (design reference).dc.html` — the full interactive design reference (all four explored directions plus the final merged design with the Agents/MCP tab). The **final direction to build is the last/topmost section in the file** (marked "2a — Merged dashboard with Agents launcher"); the earlier four side-by-side options (1a–1d) are exploration history and can be ignored for implementation.
- `screenshots/` — static captures of the final design (2a), one per tab/state:
  - `live-tab.png`, `stats-tab.png`, `tail-tab.png`, `agents-tab.png`
  - `agent-session-cli.png`, `agent-session-app.png` — the two Preview-session modal variants

## Open questions for the implementing engineer

1. **Agent launch semantics**: the primary action copies a shell command or opens a URL scheme (`claude://…`, `cursor://…`, etc.) — most of those custom URL schemes don't exist today. Decide per-agent whether "Open app ↗" is realistic (e.g. Cursor does support `cursor://file/...`) or should just fall back to "copy command" for everyone until real deep-link support exists.
2. **MCP/integrations panel**: purely a mock UI shell right now — no OAuth, no MCP server registration, no persistence. Needs real backend design (where do connected-integration credentials live? how does an agent session actually get the MCP tools?).
3. **Where does the web dashboard run**: as a new `conductor-dashboard --web` mode alongside the existing terminal UI, replacing it, or as a totally separate command? The terminal UI's polling/cursor logic in `app.py` should be reusable either way.
