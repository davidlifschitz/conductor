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


def build_parser() -> argparse.ArgumentParser:
    # Shared so `--db` works both before and after the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--db",
        default=str(ROOT / "conductor.db"),
        help="ledger path (default: $CONDUCTOR_HOME/conductor.db)",
    )

    ap = argparse.ArgumentParser(
        prog="python -m conductor.dashboard",
        description="Read-only terminal dashboard over the Conductor ledger.",
        parents=[common],
    )
    # Live defaults on the top-level parser so `parse_args([])` works
    # without a subcommand (spec §3 / test_parser_defaults).
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--days", type=int, default=1)
    ap.add_argument("--proxy", default="http://localhost:8484")
    ap.add_argument(
        "--policy",
        default=str(ROOT / "policy.yaml"),
        help="policy.yaml for ladder line",
    )
    ap.add_argument("--rows", type=int, default=200)

    sub = ap.add_subparsers(dest="cmd")

    live = sub.add_parser(
        "live",
        help="full-screen live dashboard (default)",
        parents=[common],
    )
    live.add_argument("--interval", type=float, default=1.0)
    live.add_argument("--days", type=int, default=1)
    live.add_argument("--proxy", default="http://localhost:8484")
    live.add_argument(
        "--policy",
        default=str(ROOT / "policy.yaml"),
    )
    live.add_argument("--rows", type=int, default=200)

    stats = sub.add_parser("stats", help="one-shot summary tables", parents=[common])
    stats.add_argument("--days", type=int, default=7)

    tail = sub.add_parser("tail", help="plain-text request log", parents=[common])
    tail.add_argument("-n", type=int, default=20, dest="n")
    tail.add_argument("--follow", action="store_true")
    tail.add_argument("--interval", type=float, default=1.0)

    show = sub.add_parser("show", help="full detail of one ledger row", parents=[common])
    show.add_argument("id", type=int)

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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
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
