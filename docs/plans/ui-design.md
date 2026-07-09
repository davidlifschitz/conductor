# Conductor Dashboard — CLI UI Design Spec (v0.3)

Status: ready for implementation. Scope: read-only terminal UI over the SQLite
ledger + `GET /health`. No policy editing, no ledger writes, no web UI.

---

## 1. Overview and rationale

### What we build

A single new package, `conductor/dashboard/`, exposing one entry point:

```bash
python -m conductor.dashboard            # live full-screen dashboard (default)
python -m conductor.dashboard live       # same, explicit
python -m conductor.dashboard stats      # one-shot summary tables, then exit
python -m conductor.dashboard tail       # plain-text request log (one-shot or --follow)
python -m conductor.dashboard show 123   # full detail of ledger row id=123
```

### Chosen approach: live dashboard + one-shot subcommands (both)

- The **live screen** answers "what is the proxy doing right now" — the primary
  ask. It combines a health/config header, a summary pane, and a scrolling
  request tail in one terminal page, refreshed on a poll interval.
- **Subcommands** (`stats`, `tail`, `show`) exist because a full-screen TUI is
  the wrong tool for piping, scripting, copy-pasting a row into a bug report,
  or inspecting one request. They reuse the exact same query layer, so they
  cost little extra code and make the query layer trivially unit-testable.
- Row-level detail is a subcommand (`show <id>`), **not** interactive row
  selection inside the live view. Interactive selection would force a real TUI
  framework (textual) for marginal benefit; the live table already prints the
  row id, so `show <id>` is one command away.

### Dependency decision: add `rich` (one small new dep)

- **Pure stdlib/ANSI** was rejected: hand-rolled cursor addressing, column
  sizing, truncation, color, and flicker-free repaint is exactly the risky,
  ugly-result path. It would be the largest module in the project.
- **`textual`** was rejected: it's a full app framework (async event loop,
  CSS, widget tree) — overkill for a read-only poller and a bigger dependency
  surface than the rest of Conductor combined.
- **`rich`** is the sweet spot: `rich.live.Live` + `rich.table.Table` +
  `rich.layout.Layout` give a flicker-free, attractive, auto-sized dashboard
  in ~200 lines. It is a single pure-Python package with no transitive deps
  beyond `pygments`/`markdown-it-py`, widely pinned, and consistent with the
  project's "minimal deps" spirit (fastapi already pulls in more). The README
  install line grows by one word.

`rich` is imported **only** inside `conductor/dashboard/`; the proxy, ledger,
report, and feature modules stay rich-free.

### Polling strategy

- Open SQLite **read-only** (`sqlite3.connect(f"file:{path}?mode=ro", uri=True)`)
  so the dashboard can never write or create the db.
- Live tail uses a **last-seen-id cursor**: keep `max(id)` seen so far, each
  tick run `SELECT ... WHERE id > ? ORDER BY id ASC LIMIT 500`, append to an
  in-memory ring buffer (deque, maxlen 1000). This is O(new rows) per tick and
  immune to clock skew (id is monotonic; `ts` is not guaranteed unique).
- Default interval **1.0 s** for the tail. Summary stats and `/health` are
  cheaper to compute stale, so they refresh every **5 ticks** (~5 s) to keep
  per-tick work near zero.
- Every query runs in a fresh short-lived connection (same pattern as
  `Ledger._conn`); a `sqlite3.OperationalError` (locked / missing / malformed)
  is caught per-tick and surfaced as a status-line warning — the dashboard
  never crashes on a busy or absent db.

---

## 2. Files: new / changed

| Path | Change |
|---|---|
| `conductor/dashboard/__init__.py` | NEW — empty (package marker) |
| `conductor/dashboard/__main__.py` | NEW — argparse CLI, dispatches to subcommands |
| `conductor/dashboard/data.py` | NEW — all SQL + `/health` fetch + plain-data shaping (no rich imports) |
| `conductor/dashboard/render.py` | NEW — turns plain data into rich renderables / plain strings |
| `conductor/dashboard/app.py` | NEW — live loop: polling, keyboard, Layout assembly |
| `tests/test_dashboard.py` | NEW — pytest suite for `data.py` + `render.py` |
| `tests/__init__.py` | NEW if `tests/` doesn't exist — empty |
| `README.md` | CHANGED — add `rich` to the pip install line; add a `## Dashboard` section (see §9); update "Still open" to drop the Dashboard bullet |

No changes to `proxy.py`, `ledger.py`, `report.py`, `policy.yaml`, or
`pricing.yaml`.

Dependency addition: `rich` (document in README install line:
`pip install fastapi uvicorn httpx pyyaml rich`).

---

## 3. CLI grammar

All paths default relative to `ROOT = Path(os.environ.get("CONDUCTOR_HOME",
Path(__file__).resolve().parents[2]))` — the same convention as `report.py`
(note `parents[2]` because the module is one level deeper).

```
python -m conductor.dashboard [SUBCOMMAND] [OPTIONS]

Global options (valid on every subcommand):
  --db PATH          ledger path            (default: $CONDUCTOR_HOME/conductor.db or repo root)

Subcommands:
  live (default when no subcommand given)
      --interval SECONDS   poll interval, float          (default: 1.0)
      --days N             stats window, int             (default: 1)
      --proxy URL          proxy base URL for /health    (default: http://localhost:8484)
      --policy PATH        policy.yaml for ladder line   (default: $CONDUCTOR_HOME/policy.yaml)
      --rows N             max tail rows kept/displayed  (default: 200)

  stats
      --days N             window, int                   (default: 7)

  tail
      -n N                 rows to print                 (default: 20)
      --follow             keep polling and print new rows (Ctrl-C to stop)
      --interval SECONDS   poll interval with --follow   (default: 1.0)

  show ID                  positional int: ledger row id
```

Exit codes: `0` normal; `1` operational error (e.g. `show` on a nonexistent
id, unreadable db for one-shot commands); `130` on Ctrl-C is fine (default
Python behavior is acceptable — catch `KeyboardInterrupt` in `live`/`--follow`
and exit 0 cleanly instead).

---

## 4. Module breakdown

### 4.1 `conductor/dashboard/data.py` — queries and plain data (no rich, no printing)

```python
"""Read-only data access for the dashboard.

Every function opens its own short-lived read-only SQLite connection and
returns plain Python data (dataclasses / lists / dicts) so it can be unit
tested against a tmp db with no proxy and no terminal.
"""

from dataclasses import dataclass

@dataclass
class RequestRow:
    """One ledger row, typed. Field order mirrors SELECT order in ROW_COLS."""
    id: int
    ts: float
    harness: str | None
    tag: str | None
    rule: str | None
    requested_model: str | None
    routed_model: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    latency_ms: int | None
    stream: int | None
    status: int | None
    est_input_tokens: int | None

    @property
    def escalated(self) -> bool:
        """True when this row is an escalation retry (rule 'escalated:<reason>')."""

@dataclass
class Summary:
    """Aggregates over a window: per-model rows, per-rule rows, totals."""
    by_model: list[tuple[str, int, int, int, float | None, int]]
        # (routed_model, calls, in_tok, out_tok, cost_or_None, unpriced_count)
    by_rule: list[tuple[str, int, float | None]]
        # (rule, calls, cost_or_None)
    total_calls: int
    total_cost: float | None      # None when every row is unpriced
    escalation_count: int
    error_count: int              # status is not 200 (NULL counts as error)

@dataclass
class Health:
    """Proxy /health probe result."""
    up: bool
    default_model: str | None     # from /health JSON when up
    error: str | None             # short reason when down ('connect timeout', ...)

ROW_COLS = ("id, ts, harness, tag, rule, requested_model, routed_model, "
            "input_tokens, output_tokens, cost_usd, latency_ms, stream, "
            "status, est_input_tokens")

def connect_ro(db_path: str) -> "sqlite3.Connection":
    """Open db read-only via URI. Raises sqlite3.OperationalError if the file
    does not exist or cannot be opened (callers decide how to surface it)."""

def db_exists(db_path: str) -> bool:
    """os.path.isfile check so the live view can show 'waiting for ledger'."""

def fetch_new_rows(db_path: str, after_id: int, limit: int = 500) -> list[RequestRow]:
    """Tail cursor. Rows with id > after_id, ascending, capped at limit."""

def fetch_recent_rows(db_path: str, n: int) -> list[RequestRow]:
    """Last n rows by id (for `tail` and initial live backfill), ascending order."""

def fetch_row(db_path: str, row_id: int) -> RequestRow | None:
    """Single row for `show`; None when the id doesn't exist."""

def fetch_summary(db_path: str, since_ts: float) -> Summary:
    """All aggregate queries for the stats pane / `stats` subcommand."""

def fetch_health(proxy_url: str, timeout: float = 0.8) -> Health:
    """Sync GET {proxy_url}/health via httpx. Never raises: any exception or
    non-200 becomes Health(up=False, error=<short class name / status>)."""

def load_ladder(policy_path: str) -> list[str]:
    """escalation.ladder from policy.yaml; [] when file missing/unparseable."""
```

Concrete SQL used by these functions:

```sql
-- fetch_new_rows
SELECT {ROW_COLS} FROM requests WHERE id > ? ORDER BY id ASC LIMIT ?;

-- fetch_recent_rows (subquery keeps final ordering ascending)
SELECT * FROM (
    SELECT {ROW_COLS} FROM requests ORDER BY id DESC LIMIT ?
) ORDER BY id ASC;

-- fetch_row
SELECT {ROW_COLS} FROM requests WHERE id = ?;

-- fetch_summary: by_model (same shape as report.py, ordered by cost)
SELECT routed_model, COUNT(*), COALESCE(SUM(input_tokens),0),
       COALESCE(SUM(output_tokens),0), SUM(cost_usd), SUM(cost_usd IS NULL)
FROM requests WHERE ts >= ?
GROUP BY routed_model ORDER BY SUM(cost_usd) DESC;

-- fetch_summary: by_rule
SELECT rule, COUNT(*), SUM(cost_usd)
FROM requests WHERE ts >= ?
GROUP BY rule ORDER BY 2 DESC;

-- fetch_summary: totals / escalations / errors (one row)
SELECT COUNT(*),
       SUM(cost_usd),
       SUM(CASE WHEN rule LIKE 'escalated:%' THEN 1 ELSE 0 END),
       SUM(CASE WHEN status IS NULL OR status != 200 THEN 1 ELSE 0 END)
FROM requests WHERE ts >= ?;
```

All windowed queries use the existing `idx_requests_ts` index; the tail cursor
uses the integer primary key. No new indexes required.

### 4.2 `conductor/dashboard/render.py` — formatting (pure, returns rich objects or strings)

```python
"""Rendering: plain data in, rich renderables (or plain strings) out.

No I/O, no sqlite, no sleeping — every function is unit-testable by asserting
on rendered text (rich Console(record=True) or the plain-string helpers).
"""

def fmt_cost(c: float | None) -> str:
    """'$0.0123' or '?' — identical semantics to report.fmt_cost."""

def fmt_tokens(n: int | None) -> str:
    """'12,345' or '?' when None."""

def fmt_clock(ts: float) -> str:
    """Local 'HH:MM:SS' from an epoch float."""

def fmt_latency(ms: int | None) -> str:
    """'842ms' / '12.3s' / '?'."""

def short(s: str | None, width: int) -> str:
    """Truncate with a trailing ellipsis; '' for None."""

def row_style(row: RequestRow) -> str:
    """rich style string for a tail row: 'yellow' when row.escalated,
    'red' when status not 200/None-status, '' otherwise."""

def tail_table(rows: list[RequestRow], max_rows: int) -> "rich.table.Table":
    """The live-tail table (columns per mockup §5.1). Shows the LAST max_rows
    entries. Escalated rows get a '⤴' marker in the rule column plus yellow
    style; error rows red."""

def summary_panels(summary: Summary, days: int) -> "rich.console.Group":
    """Two side-by-side tables ('by model', 'by rule') plus a totals line."""

def header_bar(health: Health, ladder: list[str], db_path: str,
               paused: bool, warn: str | None) -> "rich.text.Text":
    """One-line status: proxy UP/DOWN, default model, ladder, db path,
    PAUSED flag, and any per-tick warning (e.g. 'db locked, retrying')."""

def detail_text(row: RequestRow) -> str:
    """Plain multi-line string for `show` (mockup §5.4). Also reused by
    tests since it's terminal-independent."""

def stats_text(summary: Summary, days: int) -> str:
    """Plain-text version of the summary for the `stats` subcommand
    (renders the same tables via a non-live rich Console)."""
```

### 4.3 `conductor/dashboard/app.py` — the live loop

```python
"""Live dashboard: poll loop, keyboard handling, layout assembly."""

class KeyReader:
    """Non-blocking single-key reader. On POSIX: termios cbreak + select
    with 0 timeout, restored on exit (context manager). On platforms
    without termios: .read() always returns None (Ctrl-C still works)."""
    def __enter__(self) -> "KeyReader": ...
    def __exit__(self, *exc) -> None: ...
    def read(self) -> str | None: ...

class LiveDashboard:
    """Owns mutable state: ring buffer of RequestRow (deque maxlen=1000),
    last seen id, cached Summary/Health/ladder, paused + escalations-only
    flags, last warning string."""

    def __init__(self, db_path: str, proxy_url: str, policy_path: str,
                 interval: float, days: int, rows: int): ...

    def tick(self) -> None:
        """One poll cycle: fetch_new_rows (unless paused) and, every 5th
        tick, refresh Summary + Health + ladder. sqlite3.OperationalError
        -> set self.warn, keep old data."""

    def handle_key(self, key: str | None) -> bool:
        """'q' -> return False (quit). 'p' -> toggle paused.
        'e' -> toggle escalations-only tail filter. Else True."""

    def render(self) -> "rich.layout.Layout":
        """Assemble header / summary / tail / footer via render.py."""

    def run(self) -> None:
        """rich.live.Live(screen=True) loop: tick -> render -> sleep in
        ~0.1s slices while polling KeyReader, until quit or Ctrl-C."""

def run_live(args: "argparse.Namespace") -> int: ...
```

Startup behavior: if `db_exists()` is false, the tail pane shows
`waiting for ledger at <path>… (start the proxy to create it)` and the loop
keeps polling — the db appears the moment the proxy handles a request.
Initial backfill: `fetch_recent_rows(db, rows)` seeds the buffer, and the
cursor starts at the max id seen (or 0).

### 4.4 `conductor/dashboard/__main__.py` — CLI

```python
"""argparse wiring.

build_parser() -> argparse.ArgumentParser   # separate for testability
main(argv: list[str] | None = None) -> int  # dispatch: live/stats/tail/show
"""
```

Dispatch details:

- No subcommand ⇒ behave as `live` (use `set_defaults(cmd="live")` on the
  top-level parser and make subparsers optional).
- `stats`: `fetch_summary` + print `stats_text`; exit 0. Missing db ⇒ print
  `no ledger at <path> — has the proxy handled any requests?` to stderr, exit 1.
- `tail`: print header line + one formatted line per row (plain text, no
  live screen). With `--follow`, loop on the id cursor at `--interval`,
  print rows as they arrive; Ctrl-C exits 0.
- `show ID`: `fetch_row`; not found ⇒ `no request with id <ID>` on stderr,
  exit 1.

---

## 5. Screen mockups

### 5.1 `live` (full screen, ~100×30 shown)

```
 conductor ⏵ proxy UP  default=claude-haiku-4-5  ladder: haiku-4-5 → sonnet-4-6 → fable-5   db: conductor.db
─────────────────────────────────────────────────────────────────────────────────────────────────────────────
  last 1 day        calls: 214   cost: $1.8342   escalations: 6   errors: 2
 ┌─ by model ────────────────────────────────┐ ┌─ by rule ─────────────────────────────────┐
 │ model               calls     cost  unprc │ │ rule                       calls     cost │
 │ claude-fable-5         31  $1.2100      0 │ │ default                      142  $0.2210 │
 │ claude-sonnet-4-6      41  $0.4022      0 │ │ planning-language             29  $1.1050 │
 │ claude-haiku-4-5      142  $0.2210      0 │ │ big-context                   22  $0.3801 │
 │ some-openrouter-x       0        ?      3 │ │ explicit-frontier             15  $0.1041 │
 └───────────────────────────────────────────┘ │ escalated:truncated            4  $0.0180 │
                                               │ escalated:refusal              2  $0.0060 │
                                               └───────────────────────────────────────────┘
 ┌─ requests (newest last) ──────────────────────────────────────────────────────────────────────────────────┐
 │   id  time      harness          rule                  req→routed model            tok in/out  cost    ms │
 │  208  13:01:02  claude-cli/1.2   default               sonnet→claude-haiku-4-5      1,204/310  $0.0021 842│
 │  209  13:01:05  claude-cli/1.2   planning-language     sonnet→claude-fable-5       8,401/1,922 $0.0412 5.1s│
 │  210  13:01:06  aider/0.60       default               gpt-4o→claude-haiku-4-5        922/104  $0.0009 611│
 │  211  13:01:09  claude-cli/1.2   default               sonnet→claude-haiku-4-5        455/12   $0.0002 302│
 │⤴ 212  13:01:11  claude-cli/1.2   escalated:truncated   sonnet→claude-sonnet-4-6       455/388  $0.0071 1.9s│
 │  213  13:01:14  aider/0.60       big-context           gpt-4o→claude-sonnet-4-6    71,024/902  $0.1120 9.4s│
 │  214  13:01:15  claude-cli/1.2   default               sonnet→claude-haiku-4-5        310/44   $0.0003 288│
 └───────────────────────────────────────────────────────────────────────────────────────────────────────────┘
  q quit · p pause · e escalations-only        refreshed 13:01:16 · interval 1.0s
```

Escalated rows (`⤴`, rule `escalated:*`) are yellow; non-200 rows red;
`cost` shows `?` for unpriced rows. When proxy is down the header reads
`proxy DOWN (ConnectError)` in red, everything else keeps working. When the
db is missing the requests panel body is replaced by the waiting message.

### 5.2 `stats` (one-shot, plain)

```
== conductor stats: last 7 day(s) ==   db: /Users/x/Downloads/conductor/conductor.db

calls: 1,402   cost: $12.4410   escalations: 41   errors: 9

by model
  model                    calls       in tok      out tok        cost  unpriced
  claude-fable-5             204      912,441      201,332     $8.1200         0
  claude-sonnet-4-6          311    1,204,112       88,213     $3.1000         0
  claude-haiku-4-5           880      704,220      121,331     $1.2210         0
  mystery-model                7       10,022        2,113           ?         7

by rule
  rule                          calls        cost
  default                         880     $1.2210
  planning-language               190     $7.8000
  big-context                     201     $2.9000
  explicit-frontier                90     $0.4900
  escalated:truncated              28     $0.0210
  escalated:empty                  13     $0.0100
```

### 5.3 `tail -n 3` (plain, pipeable; `--follow` appends the same line format)

```
   id  time      harness         rule                 req→routed                    in/out        cost      ms  st
  212  13:01:11  claude-cli/1.2  escalated:truncated  sonnet→claude-sonnet-4-6      455/388    $0.0071   1922  200
  213  13:01:14  aider/0.60      big-context          gpt-4o→claude-sonnet-4-6   71,024/902    $0.1120   9401  200
  214  13:01:15  claude-cli/1.2  default              sonnet→claude-haiku-4-5       310/44     $0.0003    288  200
```

### 5.4 `show 212`

```
request #212
  time              2026-07-09 13:01:11 (local)
  harness           claude-cli/1.2 (external, darwin)
  tag               —
  rule              escalated:truncated        ← escalation retry
  requested model   claude-sonnet-4-6
  routed model      claude-sonnet-4-6
  stream            no
  status            200
  latency           1922 ms
  tokens            455 in / 388 out (est. input at routing: 460)
  cost              $0.0071
```

`—` for NULL text fields, `?` for NULL numerics/cost.

---

## 6. Error / edge behavior

| Condition | Behavior |
|---|---|
| `conductor.db` missing | `live`: waiting message in tail pane, keep polling. `stats`/`tail`/`show`: stderr message, exit 1. Never create the file (read-only URI mode guarantees this). |
| Empty `requests` table | Tables render with headers and a dim `no requests yet` body line; totals show 0 / `$0.0000`. |
| DB locked (`sqlite3.OperationalError: database is locked`) | Live: skip the tick, set header warning `db busy, retrying`, clear it on next success. One-shots: retry once after 0.2 s, then fail with the message on stderr, exit 1. |
| `cost_usd` NULL | Render `?` everywhere (matches report.py); `SUM(cost_usd)` over only-NULL windows returns NULL → total prints `?`. |
| Proxy down / timeout | `Health(up=False, error=...)`; header shows red `proxy DOWN (<error>)`; ladder still shown from policy.yaml; everything ledger-side unaffected. |
| `policy.yaml` missing/bad | `load_ladder` returns `[]`; header omits the ladder segment. |
| Terminal too narrow | rich handles column shrinking/truncation natively; `short()` keeps harness/rule cells bounded. No explicit minimum size handling required. |
| Non-POSIX stdin (no termios) | KeyReader degrades to no-op; footer shows only `Ctrl-C quit`. |
| NULL `status` (crash mid-log) | Counted as an error, shown as `?` in the status column, red row. |

---

## 7. Test plan — `tests/test_dashboard.py` (pytest, tmp sqlite, no proxy, no terminal)

Fixture: `seeded_db(tmp_path)` builds a db via the real `Ledger` class
(`Ledger(tmp_path / "test.db")`, then `.record(...)` calls) with a known set:
e.g. 6 rows across 2 models / 3 rules, one `escalated:truncated` row, one
NULL-cost row, one status-500 row, one old row (ts 10 days back).

Data-layer tests (`data.py`):

1. `test_fetch_new_rows_cursor` — after_id=0 returns all ascending; after_id=max returns []; new insert then appears with the old cursor.
2. `test_fetch_recent_rows_order_and_limit` — n=3 returns the last 3 in ascending id order.
3. `test_fetch_row_found_and_missing` — returns typed `RequestRow` for a real id; `None` for id 9999.
4. `test_fetch_summary_windows` — `since` excluding the old row changes `total_calls`; escalation_count==1; error_count==1; NULL-cost model reports unpriced=1.
5. `test_summary_all_null_costs` — a window with only NULL-cost rows yields `total_cost is None`.
6. `test_connect_ro_missing_db` — `connect_ro` on a nonexistent path raises `sqlite3.OperationalError` and does NOT create the file; `db_exists` is False.
7. `test_escalated_property` — `rule='escalated:refusal'` ⇒ True; `rule='default'` ⇒ False; `rule=None` ⇒ False.
8. `test_fetch_health_down` — `fetch_health("http://127.0.0.1:1")` returns `up=False` with a non-empty error (no exception). *(No live-proxy test; the up-path is covered by the acceptance checklist.)*
9. `test_load_ladder` — reads ladder from a tmp policy.yaml; missing file ⇒ [].

Render-layer tests (`render.py`) — assert on strings, use
`rich.console.Console(record=True, width=120)` + `export_text()` for tables:

10. `test_fmt_helpers` — `fmt_cost(None)=='?'`, `fmt_cost(0.01234)=='$0.0123'`, `fmt_tokens(None)=='?'`, `fmt_latency(1922)=='1.9s'`, `fmt_latency(288)=='288ms'`, `short('abcdef',4)=='abc…'`.
11. `test_tail_table_contains_rows_and_marker` — rendered text contains the row id, harness, `⤴` for the escalated row, `?` for the unpriced cost.
12. `test_row_style` — yellow for escalated, red for status 500 and status None, '' for a normal 200 row.
13. `test_detail_text` — `show` text contains every labeled field and `—`/`?` for NULLs.
14. `test_stats_text_empty_db` — an empty db renders headers plus `no requests yet` without raising.

CLI tests (`__main__.py`):

15. `test_parser_defaults` — `build_parser().parse_args([])` ⇒ cmd `live`, interval 1.0, days 1; `['stats']` ⇒ days 7; `['show','5']` ⇒ id 5.
16. `test_main_show_missing_id_exit_code` — `main(['show','9999','--db',str(db)])` returns 1.
17. `test_main_stats_smoke` (capsys) — `main(['stats','--db',str(db)])` returns 0 and stdout contains a model name from the seed.

The live loop (`app.py`) is deliberately thin over tested parts; it gets a
single smoke test: `test_live_dashboard_tick` — construct `LiveDashboard`
against the seeded db with a stubbed `fetch_health` (monkeypatch), call
`tick()` then `render()`, assert no exception and that the layout renders via
a recording console. No pty/keyboard testing.

---

## 8. Implementation task breakdown (ordered)

1. Create `conductor/dashboard/__init__.py` (empty) and `data.py`: `ROW_COLS`, `RequestRow`, `connect_ro`, `db_exists`, `fetch_new_rows`, `fetch_recent_rows`, `fetch_row` with the SQL from §4.1.
2. Add `Summary` + `fetch_summary`, `Health` + `fetch_health` (httpx sync, catch-all), `load_ladder` to `data.py`.
3. Create `tests/test_dashboard.py` with the `seeded_db` fixture and tests 1–9; run `pytest tests/test_dashboard.py` — data layer green.
4. Create `render.py`: format helpers (`fmt_cost`, `fmt_tokens`, `fmt_clock`, `fmt_latency`, `short`, `row_style`), then `detail_text` and `stats_text`.
5. Add `tail_table`, `summary_panels`, `header_bar` (rich) to `render.py`; add tests 10–14 — render layer green.
6. Create `__main__.py`: `build_parser`, `main` with `stats`, `tail` (incl. `--follow`), `show` wired to data+render; add tests 15–17.
7. Create `app.py`: `KeyReader`, `LiveDashboard` (`tick`/`handle_key`/`render`/`run`), wire `live` as the default subcommand; add the `test_live_dashboard_tick` smoke test.
8. Manual verification pass (see acceptance criteria): run against the real proxy, exercise missing-db, proxy-down, pause, escalations-only, narrow terminal.
9. README: add `rich` to the install line, add the `## Dashboard` section (§9), remove the Dashboard bullet from "Still open". Full `pytest` run green.

Each task is a safe commit point; 1–3 must precede 4–7.

## 9. README addition (paste-ready)

```markdown
## Dashboard

    pip install rich          # one-time; the proxy itself doesn't need it
    python -m conductor.dashboard

Full-screen live view: proxy health, spend by model/rule, and a real-time
tail of every request (escalations highlighted). Keys: q quit, p pause,
e escalations-only. One-shot variants:

    python -m conductor.dashboard stats --days 7
    python -m conductor.dashboard tail -n 50 --follow
    python -m conductor.dashboard show 212

Read-only over conductor.db and GET /health — safe to run anytime, even
while the proxy is down. Respects CONDUCTOR_HOME like conductor.report.
```

## 10. Acceptance criteria checklist

- [ ] `python -m conductor.dashboard` opens a full-screen live view showing header (proxy status, default model, ladder, db path), summary panes, and request tail; refreshes ~1 s without flicker.
- [ ] New requests through the proxy appear in the tail within ~2 s, with time, harness, rule, requested→routed model, tokens in/out, cost, latency, status.
- [ ] Escalated rows (`rule LIKE 'escalated:%'`) are visually distinct (yellow + `⤴`); error rows (status ≠ 200 or NULL) are red.
- [ ] `q` quits cleanly restoring the terminal; `p` pauses tail updates; `e` filters tail to escalations; Ctrl-C also exits cleanly.
- [ ] `stats --days N` prints by-model and by-rule tables plus totals/escalations/errors and exits 0.
- [ ] `tail -n N` prints the last N rows plainly; `tail --follow` streams new rows; both pipeable.
- [ ] `show <id>` prints all ledger fields for the row; unknown id exits 1 with a message.
- [ ] Missing db: live view waits and recovers automatically; one-shot commands exit 1 with a clear message; the dashboard never creates or writes the db file (read-only URI mode).
- [ ] NULL costs render as `?` everywhere; an all-unpriced window shows total `?`.
- [ ] Proxy down: header shows red DOWN with reason; ledger views keep working.
- [ ] Locked db never crashes the live view (warning + retry).
- [ ] `--db`, `--days`, `--interval`, `--proxy`, `--rows`, `-n`, `--follow` behave per §3; `CONDUCTOR_HOME` is respected for db and policy defaults.
- [ ] `pytest tests/test_dashboard.py` passes with no proxy running and no terminal interaction; only `rich` added as a dependency, imported nowhere outside `conductor/dashboard/`.
- [ ] README updated per §9.
