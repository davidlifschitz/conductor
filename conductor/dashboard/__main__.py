"""argparse wiring.

build_parser() -> argparse.ArgumentParser   # separate for testability
main(argv: list[str] | None = None) -> int  # dispatch: live/stats/tail/show
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

from . import data, render

# parents[2]: dashboard/__main__.py -> dashboard -> conductor -> repo root
ROOT = Path(os.environ.get("CONDUCTOR_HOME", Path(__file__).resolve().parents[2]))


# Defaults defined once. Subparser copies use SUPPRESS so a pre-subcommand
# flag is not overwritten by the subparser's default (same pattern as --db).
DEFAULT_INTERVAL = 1.0
DEFAULT_DAYS_LIVE = 1
DEFAULT_DAYS_STATS = 7
DEFAULT_PROXY = "http://localhost:8484"
DEFAULT_POLICY = str(ROOT / "policy.yaml")
DEFAULT_ROWS = 200
DEFAULT_TAIL_N = 20
DEFAULT_TAIL_INTERVAL = 1.0
DEFAULT_WEB_HOST = "127.0.0.1"
DEFAULT_WEB_PORT = 8485
DEFAULT_PROJECT = os.environ.get("CONDUCTOR_PROJECT") or ""


def _days_flag_present(argv: list[str]) -> bool:
    return any(a == "--days" or a.startswith("--days=") for a in argv)


def _apply_cmd_defaults(args: argparse.Namespace, argv: list[str]) -> argparse.Namespace:
    """stats defaults to --days 7 when the flag was omitted entirely.

    Top-level --days defaults to live's 1; stats' subparser uses SUPPRESS so a
    pre-subcommand `--days N` is preserved. When stats is chosen with no
    --days at all, bump to 7.
    """
    if args.cmd == "stats" and not _days_flag_present(argv):
        args.days = DEFAULT_DAYS_STATS
    return args


class _DashboardParser(argparse.ArgumentParser):
    """Applies stats --days default after parse so build_parser().parse_args
    matches main() behavior."""

    def parse_args(self, args=None, namespace=None):  # type: ignore[override]
        raw = sys.argv[1:] if args is None else list(args)
        ns = super().parse_args(args, namespace)
        return _apply_cmd_defaults(ns, raw)


def build_parser() -> argparse.ArgumentParser:
    # `--db` is global (spec §3). Top-level carries the default; subparser
    # copies use SUPPRESS so a pre-subcommand `--db` is not overwritten.
    db_help = "ledger path (default: $CONDUCTOR_HOME/conductor.db)"
    db_default = str(ROOT / "conductor.db")
    top_db = argparse.ArgumentParser(add_help=False)
    top_db.add_argument("--db", default=db_default, help=db_help)
    sub_db = argparse.ArgumentParser(add_help=False)
    sub_db.add_argument("--db", default=argparse.SUPPRESS, help=db_help)

    ap = _DashboardParser(
        prog="python -m conductor.dashboard",
        description="Read-only terminal dashboard over the Conductor ledger.",
        parents=[top_db],
    )
    # Live defaults on the top-level parser so `parse_args([])` works
    # without a subcommand (spec §3 / test_parser_defaults).
    ap.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS_LIVE)
    ap.add_argument("--proxy", default=DEFAULT_PROXY)
    ap.add_argument(
        "--policy",
        default=DEFAULT_POLICY,
        help="policy.yaml for ladder line",
    )
    ap.add_argument("--rows", type=int, default=DEFAULT_ROWS)
    ap.add_argument("--web", action="store_true", help="serve the web GUI instead of the TUI")
    ap.add_argument("--host", default=DEFAULT_WEB_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_WEB_PORT)
    ap.add_argument(
        "--project",
        default=DEFAULT_PROJECT or None,
        help="project dir for Agents launch commands "
        "(default: $CONDUCTOR_PROJECT / last used / cwd)",
    )

    sub = ap.add_subparsers(dest="cmd")

    live = sub.add_parser(
        "live",
        help="full-screen live dashboard (default)",
        parents=[sub_db],
    )
    live.add_argument("--interval", type=float, default=argparse.SUPPRESS)
    live.add_argument("--days", type=int, default=argparse.SUPPRESS)
    live.add_argument("--proxy", default=argparse.SUPPRESS)
    live.add_argument("--policy", default=argparse.SUPPRESS)
    live.add_argument("--rows", type=int, default=argparse.SUPPRESS)

    stats = sub.add_parser("stats", help="one-shot summary tables", parents=[sub_db])
    # SUPPRESS so `--days N stats` is not overwritten; default 7 applied in
    # _apply_cmd_defaults when --days was not on the command line.
    stats.add_argument("--days", type=int, default=argparse.SUPPRESS)

    tail = sub.add_parser("tail", help="plain-text request log", parents=[sub_db])
    tail.add_argument("-n", type=int, default=DEFAULT_TAIL_N, dest="n")
    tail.add_argument("--follow", action="store_true")
    tail.add_argument("--interval", type=float, default=DEFAULT_TAIL_INTERVAL)

    show = sub.add_parser("show", help="full detail of one ledger row", parents=[sub_db])
    show.add_argument("id", type=int)

    web = sub.add_parser("web", help="web GUI (browser dashboard + agents)", parents=[sub_db])
    web.add_argument("--host", default=argparse.SUPPRESS)
    web.add_argument("--port", type=int, default=argparse.SUPPRESS)
    web.add_argument("--proxy", default=argparse.SUPPRESS)
    web.add_argument("--policy", default=argparse.SUPPRESS)
    web.add_argument("--project", default=argparse.SUPPRESS)

    # After add_subparsers so dest="cmd" default is live when omitted.
    ap.set_defaults(cmd="live")
    return ap


def _with_db_retry(fn, *args, **kwargs):
    """One-shot commands: retry once after 0.2s on OperationalError."""
    try:
        return fn(*args, **kwargs)
    except sqlite3.OperationalError:
        time.sleep(0.2)
        return fn(*args, **kwargs)


def _cmd_stats(args: argparse.Namespace) -> int:
    if not data.db_exists(args.db):
        print(
            f"no ledger at {args.db} — has the proxy handled any requests?",
            file=sys.stderr,
        )
        return 1
    try:
        since = time.time() - args.days * 86400
        summary = _with_db_retry(data.fetch_summary, args.db, since)
    except sqlite3.OperationalError as e:
        print(f"db error: {e}", file=sys.stderr)
        return 1
    text = render.stats_text(summary, args.days)
    lines = text.splitlines()
    if lines:
        lines[0] = f"{lines[0]}   db: {args.db}"
        text = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    print(text, end="" if text.endswith("\n") else "\n")
    return 0


def _cmd_tail(args: argparse.Namespace) -> int:
    if not data.db_exists(args.db):
        print(
            f"no ledger at {args.db} — has the proxy handled any requests?",
            file=sys.stderr,
        )
        return 1
    try:
        rows = _with_db_retry(data.fetch_recent_rows, args.db, args.n)
    except sqlite3.OperationalError as e:
        print(f"db error: {e}", file=sys.stderr)
        return 1

    print(render.plain_tail_header())
    for row in rows:
        print(render.plain_tail_line(row))

    if not args.follow:
        return 0

    after_id = rows[-1].id if rows else 0
    try:
        while True:
            time.sleep(args.interval)
            try:
                new = data.fetch_new_rows(args.db, after_id)
            except sqlite3.OperationalError:
                continue
            for row in new:
                print(render.plain_tail_line(row), flush=True)
                after_id = row.id
    except KeyboardInterrupt:
        return 0


def _cmd_show(args: argparse.Namespace) -> int:
    if not data.db_exists(args.db):
        print(
            f"no ledger at {args.db} — has the proxy handled any requests?",
            file=sys.stderr,
        )
        return 1
    try:
        row = _with_db_retry(data.fetch_row, args.db, args.id)
    except sqlite3.OperationalError as e:
        print(f"db error: {e}", file=sys.stderr)
        return 1
    if row is None:
        print(f"no request with id {args.id}", file=sys.stderr)
        return 1
    print(render.detail_text(row))
    return 0


def _cmd_live(args: argparse.Namespace) -> int:
    from .app import run_live

    return run_live(args)


def _cmd_web(args: argparse.Namespace) -> int:
    from conductor.webui.server import run_web

    return run_web(args)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "web" or (args.cmd == "live" and getattr(args, "web", False)):
        return _cmd_web(args)
    if args.cmd == "live":
        return _cmd_live(args)
    if args.cmd == "stats":
        return _cmd_stats(args)
    if args.cmd == "tail":
        return _cmd_tail(args)
    if args.cmd == "show":
        return _cmd_show(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
