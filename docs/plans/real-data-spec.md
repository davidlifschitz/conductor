# Spec: Wire Conductor Web GUI to Real Data (v0.4)

Status: ready for implementation. Scope: replace mock/hardcoded Agents-tab
data with live machine state, and deepen Live/Stats/Tail with real ledger
fields that are already available but underused. No OAuth for third-party
integrations in this pass.

---

## 0. Current state (what is already real vs mock)

| Surface | Status today |
|---|---|
| Live / Stats / Tail tabs | **Real** — poll `conductor.db` + `GET /health` via `/api/*` |
| KPI strip, by-model/by-rule, spend sparkline | **Real** |
| Row-detail modal | **Real** (ledger fields) |
| Agents — CLI/APP toggle, copy launch command | **Half-real** — copies a real-looking command, but project dir is hardcoded `~/projects/payment-service` |
| Agents — "Open app ↗" | **Mostly fake** — only Cursor/T3 have plausible schemes; others invent `claude://` etc. |
| Agents — "Preview session" modal | **100% mock** — hardcoded payment_service.py transcript |
| MCP panel — Google Drive / GitHub / Linear / Plaid | **100% mock** — localStorage booleans, no real connection |
| MCP panel — custom MCP add/remove | **Browser-only** — localStorage, never reaches any agent |

This machine already has real CLIs on PATH: `claude`, `cursor`, `codex`,
`hermes`, `openclaw`. Ledger at `~/.conductor/conductor.db` has live rows.
Policy/pricing/context live under `~/.conductor/`.

---

## 1. Goals

1. **Project context is real** — launch commands and target lines use a
   user-selected (or auto-detected) project directory, not a fake path.
2. **Agent availability is real** — cards show which agents are installed
   (binary on PATH / known app present) vs missing.
3. **Launch commands are real and Conductor-aware** — always inject
   `ANTHROPIC_BASE_URL=http://localhost:8484` (or the configured proxy) so
   sessions actually hit the ledger.
4. **Preview session uses real ledger data** — replace the fake transcript
   with the last N requests for that harness (or a "no sessions yet" empty
   state).
5. **Custom MCP list is persisted server-side** under `~/.conductor/`, not
   only in the browser.
6. **Built-in integration cards** stay visual/status-only for v0.4 (no OAuth)
   but read connection status from real local config files when detectable;
   otherwise show "Not connected" honestly (never fake Connected).

Non-goals for v0.4:
- Spawning agent processes from the browser (security / sandbox).
- OAuth flows for Google Drive / GitHub / Linear / Plaid.
- Streaming a live agent TTY into the Preview modal.
- Replacing the terminal TUI.

---

## 2. New / changed files

| Path | Change |
|---|---|
| `conductor/webui/agents_data.py` | NEW — pure helpers: detect installed agents, resolve project dir, build launch commands, load/save MCP config, recent sessions by harness |
| `conductor/webui/server.py` | CHANGED — add `/api/agents`, `/api/agents/sessions`, `/api/mcp`, `/api/project` endpoints |
| `conductor/webui/static/agents.js` | CHANGED — fetch real data; drop hardcoded DEFAULT_PROJECT / fake Connected defaults / fake preview transcript |
| `conductor/webui/static/app.js` | CHANGED — pass proxy URL / project into agents mount if needed; optional project picker in nav |
| `conductor/dashboard/__main__.py` | CHANGED — `--project PATH` for web mode (default: cwd or `$CONDUCTOR_PROJECT`) |
| `~/.conductor/mcp.json` | NEW runtime file (not in repo) — custom MCP server list |
| `~/.conductor/webui.json` | NEW runtime file — last project path, agent mode prefs (optional; can stay in localStorage for prefs) |
| `tests/test_webui_agents.py` | NEW — unit tests for agents_data + API |
| `docs/plans/real-data-spec.md` | THIS FILE |
| `README.md` | CHANGED — note real Agents/MCP behavior |

No changes to `proxy.py`, `ledger.py`, `policy.yaml` schema.

---

## 3. Data model

### 3.1 Agent catalog (static metadata + dynamic status)

```python
@dataclass
class AgentInfo:
    id: str                 # 'claude-code', 'codex', 'cursor', ...
    name: str
    avatar: str             # 'CC'
    accent: str             # '#d97757'
    cli_bin: str | None     # 'claude' | 'codex' | 'cursor' | ...
    installed: bool         # shutil.which(cli_bin) is not None (or app bundle check)
    cli_path: str | None    # resolved path when installed
    app_scheme: str | None  # only when a real scheme exists
    app_open_url: str | None  # fully built URL for current project, or None
    default_mode: str       # 'cli' | 'app'
    harness_prefixes: list[str]  # user-agent prefixes that match this agent in the ledger
```

Harness prefix map (for session lookup against `requests.harness`):

| Agent id | Match prefixes (case-insensitive startswith / contains) |
|---|---|
| claude-code | `claude`, `claude-cli`, `claude-code` |
| codex | `codex` |
| cursor | `cursor` |
| opencode | `opencode` |
| t3chat | `t3` |
| openclaw | `openclaw` |
| hermes | `hermes` |

### 3.2 Project

```python
@dataclass
class ProjectInfo:
    path: str               # absolute expanded path
    exists: bool
    name: str               # basename
```

Resolution order for default project:
1. `--project` CLI flag / `CONDUCTOR_PROJECT` env
2. `~/.conductor/webui.json` → `last_project`
3. `cwd` of the `conductor-dashboard web` process (if it looks like a git repo)
4. `$HOME`

### 3.3 MCP config (`~/.conductor/mcp.json`)

```json
{
  "custom": [
    { "id": "uuid", "name": "Notion", "url": "https://mcp.example.com/sse" }
  ],
  "integrations": {
    "github": { "detected": true, "source": "~/.config/gh/hosts.yml" },
    "gdrive": { "detected": false },
    "linear": { "detected": false },
    "plaid": { "detected": false }
  }
}
```

v0.4 detection rules (read-only probes, never invent Connected):
- **github**: `gh auth status` exit 0 OR `~/.config/gh/hosts.yml` exists with a token entry
- **gdrive / linear / plaid**: always `detected: false` until real OAuth exists — UI shows "Not connected" and Connect button is disabled with tooltip "Coming soon"

Custom MCP list is the only writable part. Connect/Disconnect toggles for
built-ins are removed or disabled (no more fake localStorage Connected).

### 3.4 Session preview (from ledger)

```python
@dataclass
class SessionPreview:
    agent_id: str
    mode: str                 # requested preview mode (cli|app) — UI chrome only
    routed_model: str | None  # most recent matching row's routed_model
    rule: str | None
    rows: list[RequestRow]    # last ≤ 8 matching harness rows, newest last
    empty: bool
```

---

## 4. API endpoints

All under the existing web FastAPI app (`create_app`). Read-mostly;
MCP custom list is the only write.

### `GET /api/project`
```json
{ "path": "/Users/…/conductor", "exists": true, "name": "conductor" }
```

### `PUT /api/project`  body: `{ "path": "…" }`
- Expanduser, resolve, require exists + isdir → 400 otherwise
- Persist to `~/.conductor/webui.json`
- Return updated `ProjectInfo`

### `GET /api/agents`
```json
{
  "proxy_url": "http://localhost:8484",
  "project": { "path": "…", "exists": true, "name": "…" },
  "agents": [ AgentInfo, … ]
}
```

### `GET /api/agents/{id}/sessions?limit=8`
Returns `SessionPreview` for that agent (ledger query filtered by harness
prefixes). 404 if unknown agent id.

### `GET /api/mcp`
```json
{
  "integrations": [
    { "id": "github", "name": "GitHub", "avatar": "GH", "accent": "#8b949e",
      "connected": true, "connectable": false, "source": "gh auth" }
  ],
  "custom": [ { "id": "…", "name": "Notion", "url": "…" } ]
}
```

### `POST /api/mcp/custom`  body: `{ "name": "…", "url": "…" }`
- Validate non-empty name + URL (http/https/stdio:// optional later)
- Append with new uuid, write `mcp.json`, return the new entry

### `DELETE /api/mcp/custom/{id}`
- Remove and persist; 404 if missing

---

## 5. Launch command contract

For CLI mode, clipboard text MUST be:

```bash
cd <project_path> && export ANTHROPIC_BASE_URL=<proxy_url> && <cli_bin>
```

Examples on this machine:
```bash
cd /Users/thebiglipper/Downloads/conductor && export ANTHROPIC_BASE_URL=http://localhost:8484 && claude
cd /Users/thebiglipper/Downloads/conductor && export ANTHROPIC_BASE_URL=http://localhost:8484 && cursor .
cd /Users/thebiglipper/Downloads/conductor && export ANTHROPIC_BASE_URL=http://localhost:8484 && codex
```

App mode:
- **cursor**: `cursor://file/<abs_project_path>` (real scheme) → `window.open`
- **t3chat**: `https://t3.chat/...` → `window.open` (note: may not honor ANTHROPIC_BASE_URL; show a small warning chip "web app — routing not guaranteed")
- **everyone else**: fall back to copy-CLI-command (same as today when `realAppScheme: false`), never invent fake URL schemes

Installed detection:
- `shutil.which(cli_bin)` for CLI agents
- Cursor: which OR `/Applications/Cursor.app` exists
- Uninstalled agents: card stays visible but primary button disabled + badge "not installed"

---

## 6. UI changes (Agents tab)

### Project bar (new, above agent grid)
- Label: `project:` + truncated path + "Change…" button
- Change opens a small modal: text input (path) + "Use this folder" submit → `PUT /api/project`
- On success, re-fetch `/api/agents` so target lines and launch commands update

### Agent cards
- Keep layout/tokens from design
- Add status chip: `installed` (dim) or `not installed` (muted)
- Target line uses real project path
- Primary button disabled when not installed
- Remove fake Connected defaults for integrations

### Preview session modal
- Header pill: `routed via conductor → <routed_model>` from latest matching row, or `no sessions yet`
- Body (CLI chrome kept for look):
  - If rows empty: dim message `no ledger rows for this agent yet — launch it through conductor and they'll show up here`
  - Else: render last N rows as terminal-ish lines:
    ```
    #212  13:01:11  escalated:truncated  sonnet→claude-sonnet-4-6  455/388  $0.0071  1.9s
    ```
  - Do NOT show the fake payment_service.py diff
- App-mode body: same real rows, presented as a simple list (keep chat chrome optional; prefer honesty over fake bubbles)

### MCP section
- Built-in cards: Connected only when `detected: true`; Connect button disabled with "Coming soon" title when not connectable
- Custom cards: load from `/api/mcp`; Add/Remove hit POST/DELETE
- Drop localStorage as source of truth for MCP (may keep agentModes in localStorage)

---

## 7. Live / Stats / Tail deepening (small, same PR or follow-up)

Already real; optional polish that uses more of the ledger:

1. **Live header** — show `default_model` from `/api/health` next to ladder when up
2. **Tail filters** — populate model/rule options from actual summary (already partially done; ensure OpenRouter-prefixed ids display cleanly via existing `shortModel`)
3. **Row detail** — include `est_input_tokens` and `tag` (already in API payload; surface in modal if missing)
4. **Empty ledger** — when `total_calls == 0`, Live tab shows a one-line CTA: `no requests yet — start conductor-proxy and point a harness at :8484`

No new SQL required beyond the session-by-harness query.

### Session SQL

```sql
SELECT {ROW_COLS} FROM requests
WHERE ts >= ?
  AND (
    lower(coalesce(harness,'')) LIKE 'claude%'
    OR lower(coalesce(harness,'')) LIKE '%claude%'
  )
ORDER BY id DESC
LIMIT ?;
```

(Exact LIKE set built from `harness_prefixes` per agent.)

---

## 8. CLI

```bash
conductor-dashboard web --project ~/Downloads/conductor
conductor-dashboard web --project . --port 8485 --proxy http://localhost:8484
```

`--project` default resolution per §3.2. Persist last choice to
`~/.conductor/webui.json`.

---

## 9. Security / safety

- Web UI remains localhost-bound by default (`127.0.0.1`)
- `PUT /api/project` must reject paths that don't exist; do not follow
  arbitrary file reads beyond `isdir` + basename
- MCP URL stored as opaque string; no SSRF fetch of the URL in v0.4
- No endpoint that executes shell commands — launch is clipboard / URL only
- `mcp.json` / `webui.json` written with mode `0o600` when created

---

## 10. Test plan (`tests/test_webui_agents.py`)

1. `test_detect_agents_marks_which_present` — monkeypatch `shutil.which` to
   return paths for a subset; assert `installed` flags
2. `test_launch_command_includes_proxy_and_project`
3. `test_project_put_rejects_missing_path` — API 400
4. `test_project_put_persists` — writes webui.json under tmp CONDUCTOR_HOME
5. `test_mcp_custom_roundtrip` — POST then GET then DELETE
6. `test_sessions_filters_by_harness` — seed ledger with claude-cli + aider
   rows; claude-code sessions returns only claude rows
7. `test_sessions_empty` — unknown harness → empty preview, not 500
8. Existing `tests/test_webui_api.py` and `tests/test_web_cli.py` stay green;
   extend CLI test for `--project`

---

## 11. Implementation task breakdown

1. Add `conductor/webui/agents_data.py` (detect, launch cmd, project resolve,
   mcp load/save, session query) + unit tests 1–2, 6–7
2. Extend `server.py` with the five endpoints; wire CONDUCTOR_HOME paths;
   tests 3–5, 8
3. Add `--project` to web CLI; persist last project
4. Rewrite `agents.js` data layer to fetch `/api/agents`, `/api/mcp`,
   `/api/agents/:id/sessions`; project bar + honest MCP status; kill
   payment_service mock transcript
5. Small Live empty-state + row-detail field polish in `app.js`
6. README + manual verify against this machine's real `claude`/`cursor`/
   `codex` installs and `~/.conductor/conductor.db`

---

## 12. Acceptance criteria

- [ ] `conductor-dashboard web --project ~` serves Agents cards whose
      "installed" state matches `which` on this machine
- [ ] Copy launch command for Claude Code pastes a command that, when run,
      produces a new row in the ledger with harness containing `claude`
- [ ] Changing project via UI updates all card target lines without reload
- [ ] Preview session for an agent with ledger history shows real rows;
      agents with none show the empty state (no payment_service fiction)
- [ ] Custom MCP add/remove survives browser refresh (server-side mcp.json)
- [ ] Built-in integrations never show Connected unless detection succeeded
- [ ] Live/Stats/Tail still work against the real ledger; empty ledger shows CTA
- [ ] `pytest tests/test_webui_agents.py tests/test_webui_api.py tests/test_web_cli.py` green
- [ ] No new deps beyond stdlib + existing FastAPI/httpx stack

---

## 13. Open decisions (defaults chosen for v0.4)

| Question | Default in this spec |
|---|---|
| Spawn agents from UI? | No — clipboard / URL only |
| Built-in OAuth? | No — detect-only / coming soon |
| Where to store MCP list? | `~/.conductor/mcp.json` |
| Preview = live TTY? | No — last ledger rows for that harness |
| Fake URL schemes? | Removed; fall back to copy command |
| Project picker UX? | Path text field (no native folder dialog in browser without File System Access API; optional enhancement later) |
