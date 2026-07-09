"""Live dashboard: poll loop, keyboard handling, layout assembly."""

from __future__ import annotations

import select
import sqlite3
import sys
import time
from collections import deque
from datetime import datetime
from typing import TYPE_CHECKING

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from . import data, render
from .data import Health, RequestRow, Summary

if TYPE_CHECKING:
    import argparse


class KeyReader:
    """Non-blocking single-key reader. On POSIX: termios cbreak + select
    with 0 timeout, restored on exit (context manager). On platforms
    without termios: .read() always returns None (Ctrl-C still works)."""

    def __init__(self) -> None:
        self._fd = None
        self._old = None
        self._ok = False

    def __enter__(self) -> KeyReader:
        try:
            import termios
            import tty

            if not sys.stdin.isatty():
                return self
            self._fd = sys.stdin.fileno()
            self._old = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
            self._ok = True
        except Exception:
            self._ok = False
        return self

    def __exit__(self, *exc) -> None:
        if self._ok and self._old is not None and self._fd is not None:
            try:
                import termios

                termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old)
            except Exception:
                pass
        self._ok = False

    def read(self) -> str | None:
        if not self._ok:
            return None
        try:
            r, _, _ = select.select([sys.stdin], [], [], 0)
            if not r:
                return None
            ch = sys.stdin.read(1)
            return ch or None
        except Exception:
            return None


def _has_termios() -> bool:
    try:
        import termios  # noqa: F401

        return True
    except ImportError:
        return False


class LiveDashboard:
    """Owns mutable state: ring buffer of RequestRow (deque maxlen=1000),
    last seen id, cached Summary/Health/ladder, paused + escalations-only
    flags, last warning string."""

    def __init__(
        self,
        db_path: str,
        proxy_url: str,
        policy_path: str,
        interval: float,
        days: int,
        rows: int,
    ):
        self.db_path = db_path
        self.proxy_url = proxy_url
        self.policy_path = policy_path
        self.interval = interval
        self.days = days
        self.rows = rows

        self.buffer: deque[RequestRow] = deque(maxlen=1000)
        self.after_id = 0
        self.summary = Summary(
            by_model=[],
            by_rule=[],
            total_calls=0,
            total_cost=None,
            escalation_count=0,
            error_count=0,
        )
        self.health = Health(up=False, default_model=None, error="starting")
        self.ladder: list[str] = []
        self.paused = False
        self.escalations_only = False
        self.warn: str | None = None
        self._tick_n = 0
        self._last_refresh = datetime.now().strftime("%H:%M:%S")
        self._waiting_for_db = not data.db_exists(db_path)

        if not self._waiting_for_db:
            self._backfill()

    def _backfill(self) -> None:
        rows = data.fetch_recent_rows(self.db_path, self.rows)
        self.buffer.clear()
        self.buffer.extend(rows)
        self.after_id = rows[-1].id if rows else 0
        self.warn = None
        self._waiting_for_db = False

    def tick(self) -> None:
        """One poll cycle: fetch_new_rows (unless paused) and, every 5th
        tick, refresh Summary + Health + ladder. sqlite3.OperationalError
        -> set self.warn, keep old data."""
        self._tick_n += 1
        self._last_refresh = datetime.now().strftime("%H:%M:%S")

        if not data.db_exists(self.db_path):
            self._waiting_for_db = True
            self.warn = None
        else:
            try:
                if self._waiting_for_db:
                    self._backfill()
                if not self.paused:
                    new = data.fetch_new_rows(self.db_path, self.after_id)
                    for row in new:
                        self.buffer.append(row)
                        self.after_id = row.id
                    self.warn = None
            except sqlite3.OperationalError:
                self.warn = "db busy, retrying"

        if self._tick_n == 1 or self._tick_n % 5 == 0:
            self._refresh_slow()

    def _refresh_slow(self) -> None:
        if data.db_exists(self.db_path):
            try:
                since = time.time() - self.days * 86400
                self.summary = data.fetch_summary(self.db_path, since)
            except sqlite3.OperationalError:
                self.warn = "db busy, retrying"
        self.health = data.fetch_health(self.proxy_url)
        self.ladder = data.load_ladder(self.policy_path)

    def handle_key(self, key: str | None) -> bool:
        """'q' -> return False (quit). 'p' -> toggle paused.
        'e' -> toggle escalations-only tail filter. Else True."""
        if key is None:
            return True
        if key in ("q", "Q"):
            return False
        if key in ("p", "P"):
            self.paused = not self.paused
        elif key in ("e", "E"):
            self.escalations_only = not self.escalations_only
        return True

    def _tail_rows(self) -> list[RequestRow]:
        rows = list(self.buffer)
        if self.escalations_only:
            rows = [r for r in rows if r.escalated]
        return rows

    def render(self) -> Layout:
        """Assemble header / summary / tail / footer via render.py."""
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=1),
            Layout(name="summary", size=12),
            Layout(name="tail", ratio=1),
            Layout(name="footer", size=1),
        )

        layout["header"].update(
            render.header_bar(
                self.health,
                self.ladder,
                self.db_path,
                self.paused,
                self.warn,
            )
        )
        layout["summary"].update(render.summary_panels(self.summary, self.days))

        if self._waiting_for_db:
            msg = Text(
                f"waiting for ledger at {self.db_path}… "
                f"(start the proxy to create it)",
                style="dim",
            )
            layout["tail"].update(Panel(msg, title="requests (newest last)"))
        else:
            layout["tail"].update(
                render.tail_table(self._tail_rows(), max_rows=self.rows)
            )

        if _has_termios():
            keys = "q quit · p pause · e escalations-only"
        else:
            keys = "Ctrl-C quit"

        footer = Text(
            f"  {keys}        refreshed {self._last_refresh} · "
            f"interval {self.interval:.1f}s"
        )
        if self.escalations_only:
            footer.append("  [escalations-only]", style="yellow")
        layout["footer"].update(footer)
        return layout

    def run(self) -> None:
        """rich.live.Live(screen=True) loop: tick -> render -> sleep in
        ~0.1s slices while polling KeyReader, until quit or Ctrl-C."""
        console = Console()
        with KeyReader() as keys:
            try:
                with Live(
                    self.render(),
                    console=console,
                    screen=True,
                    refresh_per_second=10,
                ) as live:
                    while True:
                        self.tick()
                        live.update(self.render())
                        end = time.monotonic() + self.interval
                        while time.monotonic() < end:
                            k = keys.read()
                            if not self.handle_key(k):
                                return
                            time.sleep(0.1)
            except KeyboardInterrupt:
                return


def run_live(args: argparse.Namespace) -> int:
    dash = LiveDashboard(
        db_path=args.db,
        proxy_url=args.proxy,
        policy_path=args.policy,
        interval=args.interval,
        days=args.days,
        rows=args.rows,
    )
    try:
        dash.run()
    except KeyboardInterrupt:
        return 0
    return 0
