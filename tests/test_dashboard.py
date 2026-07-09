"""Tests for conductor.dashboard — data, render, CLI, live smoke."""

from __future__ import annotations

import sqlite3
import time

import pytest
import yaml

from conductor.ledger import Ledger


@pytest.fixture
def seeded_db(tmp_path):
    """Build a ledger with a known mix of rows for dashboard tests."""
    db = tmp_path / "test.db"
    ledger = Ledger(db)
    now = time.time()
    old = now - 10 * 86400

    # 1. old row (outside 7-day window when since = now-7d)
    ledger.record(
        ts=old,
        harness="old-harness/1",
        tag=None,
        rule="default",
        requested_model="claude-sonnet-4-6",
        routed_model="claude-haiku-4-5",
        input_tokens=100,
        output_tokens=20,
        cost_usd=0.001,
        latency_ms=200,
        stream=0,
        status=200,
        est_input_tokens=100,
    )
    # 2. normal haiku
    ledger.record(
        ts=now - 100,
        harness="claude-cli/1.2",
        tag=None,
        rule="default",
        requested_model="claude-sonnet-4-6",
        routed_model="claude-haiku-4-5",
        input_tokens=1204,
        output_tokens=310,
        cost_usd=0.0021,
        latency_ms=842,
        stream=0,
        status=200,
        est_input_tokens=1200,
    )
    # 3. sonnet via planning
    ledger.record(
        ts=now - 90,
        harness="claude-cli/1.2",
        tag=None,
        rule="planning-language",
        requested_model="claude-sonnet-4-6",
        routed_model="claude-sonnet-4-6",
        input_tokens=8401,
        output_tokens=1922,
        cost_usd=0.0412,
        latency_ms=5100,
        stream=0,
        status=200,
        est_input_tokens=8400,
    )
    # 4. escalated:truncated
    ledger.record(
        ts=now - 80,
        harness="claude-cli/1.2",
        tag=None,
        rule="escalated:truncated",
        requested_model="claude-sonnet-4-6",
        routed_model="claude-sonnet-4-6",
        input_tokens=455,
        output_tokens=388,
        cost_usd=0.0071,
        latency_ms=1922,
        stream=0,
        status=200,
        est_input_tokens=460,
    )
    # 5. NULL-cost / unpriced model
    ledger.record(
        ts=now - 70,
        harness="aider/0.60",
        tag=None,
        rule="default",
        requested_model="mystery-model",
        routed_model="mystery-model",
        input_tokens=10022,
        output_tokens=2113,
        cost_usd=None,
        latency_ms=900,
        stream=0,
        status=200,
        est_input_tokens=10000,
    )
    # 6. status 500 error
    ledger.record(
        ts=now - 60,
        harness="claude-cli/1.2",
        tag=None,
        rule="default",
        requested_model="claude-haiku-4-5",
        routed_model="claude-haiku-4-5",
        input_tokens=50,
        output_tokens=0,
        cost_usd=0.0001,
        latency_ms=100,
        stream=0,
        status=500,
        est_input_tokens=50,
    )
    return db


# --- data layer ---


def test_fetch_new_rows_cursor(seeded_db):
    from conductor.dashboard.data import fetch_new_rows

    all_rows = fetch_new_rows(str(seeded_db), after_id=0)
    assert len(all_rows) == 6
    assert [r.id for r in all_rows] == list(range(1, 7))

    max_id = all_rows[-1].id
    assert fetch_new_rows(str(seeded_db), after_id=max_id) == []

    ledger = Ledger(seeded_db)
    ledger.record(
        ts=time.time(),
        harness="x",
        rule="default",
        requested_model="m",
        routed_model="m",
        input_tokens=1,
        output_tokens=1,
        cost_usd=0.0,
        latency_ms=1,
        stream=0,
        status=200,
        est_input_tokens=1,
    )
    new = fetch_new_rows(str(seeded_db), after_id=max_id)
    assert len(new) == 1
    assert new[0].id == max_id + 1


def test_fetch_recent_rows_order_and_limit(seeded_db):
    from conductor.dashboard.data import fetch_recent_rows

    rows = fetch_recent_rows(str(seeded_db), n=3)
    assert len(rows) == 3
    assert [r.id for r in rows] == [4, 5, 6]


def test_fetch_row_found_and_missing(seeded_db):
    from conductor.dashboard.data import RequestRow, fetch_row

    row = fetch_row(str(seeded_db), 4)
    assert isinstance(row, RequestRow)
    assert row.id == 4
    assert row.rule == "escalated:truncated"
    assert fetch_row(str(seeded_db), 9999) is None


def test_fetch_summary_windows(seeded_db):
    from conductor.dashboard.data import fetch_summary

    now = time.time()
    # exclude old row (10 days back)
    s = fetch_summary(str(seeded_db), since_ts=now - 7 * 86400)
    assert s.total_calls == 5
    assert s.escalation_count == 1
    assert s.error_count == 1
    unpriced = [m for m in s.by_model if m[0] == "mystery-model"]
    assert len(unpriced) == 1
    assert unpriced[0][5] == 1  # unpriced_count

    # include everything
    s_all = fetch_summary(str(seeded_db), since_ts=0)
    assert s_all.total_calls == 6


def test_summary_all_null_costs(tmp_path):
    from conductor.dashboard.data import fetch_summary

    db = tmp_path / "null.db"
    ledger = Ledger(db)
    ledger.record(
        ts=time.time(),
        harness="h",
        rule="default",
        requested_model="m",
        routed_model="m",
        input_tokens=1,
        output_tokens=1,
        cost_usd=None,
        latency_ms=1,
        stream=0,
        status=200,
        est_input_tokens=1,
    )
    s = fetch_summary(str(db), since_ts=0)
    assert s.total_cost is None
    assert s.total_calls == 1


def test_connect_ro_missing_db(tmp_path):
    from conductor.dashboard.data import connect_ro, db_exists

    missing = tmp_path / "nope.db"
    assert db_exists(str(missing)) is False
    with pytest.raises(sqlite3.OperationalError):
        connect_ro(str(missing))
    assert not missing.exists()


def test_escalated_property():
    from conductor.dashboard.data import RequestRow

    def make(rule):
        return RequestRow(
            id=1,
            ts=0.0,
            harness=None,
            tag=None,
            rule=rule,
            requested_model=None,
            routed_model=None,
            input_tokens=None,
            output_tokens=None,
            cost_usd=None,
            latency_ms=None,
            stream=None,
            status=None,
            est_input_tokens=None,
        )

    assert make("escalated:refusal").escalated is True
    assert make("default").escalated is False
    assert make(None).escalated is False


def test_fetch_health_down():
    from conductor.dashboard.data import fetch_health

    h = fetch_health("http://127.0.0.1:1")
    assert h.up is False
    assert h.error
    assert h.default_model is None


def test_load_ladder(tmp_path):
    from conductor.dashboard.data import load_ladder

    policy = tmp_path / "policy.yaml"
    policy.write_text(
        yaml.dump(
            {
                "escalation": {
                    "ladder": ["claude-haiku-4-5", "claude-sonnet-4-6", "claude-fable-5"]
                }
            }
        )
    )
    assert load_ladder(str(policy)) == [
        "claude-haiku-4-5",
        "claude-sonnet-4-6",
        "claude-fable-5",
    ]
    assert load_ladder(str(tmp_path / "missing.yaml")) == []


# --- render layer ---


def test_fmt_helpers():
    from conductor.dashboard.render import (
        fmt_cost,
        fmt_latency,
        fmt_tokens,
        short,
    )

    assert fmt_cost(None) == "?"
    assert fmt_cost(0.01234) == "$0.0123"
    assert fmt_tokens(None) == "?"
    assert fmt_latency(1922) == "1.9s"
    assert fmt_latency(288) == "288ms"
    assert short("abcdef", 4) == "abc…"


def test_tail_table_contains_rows_and_marker(seeded_db):
    from rich.console import Console

    from conductor.dashboard.data import fetch_recent_rows
    from conductor.dashboard.render import tail_table

    rows = fetch_recent_rows(str(seeded_db), n=10)
    table = tail_table(rows, max_rows=10)
    # force_terminal=False so Console.width is honored (TTY path clamps to 80).
    console = Console(record=True, width=160, force_terminal=False)
    console.print(table)
    text = console.export_text()
    assert "4" in text  # escalated row id
    assert "claude-cli" in text or "claude-cli/1.2" in text
    assert "⤴" in text
    assert "?" in text  # unpriced cost


def test_row_style(seeded_db):
    from conductor.dashboard.data import fetch_row
    from conductor.dashboard.render import row_style

    escalated = fetch_row(str(seeded_db), 4)
    assert row_style(escalated) == "yellow"

    err = fetch_row(str(seeded_db), 6)
    assert row_style(err) == "red"

    from conductor.dashboard.data import RequestRow

    null_status = RequestRow(
        id=99,
        ts=0.0,
        harness=None,
        tag=None,
        rule="default",
        requested_model=None,
        routed_model=None,
        input_tokens=None,
        output_tokens=None,
        cost_usd=None,
        latency_ms=None,
        stream=None,
        status=None,
        est_input_tokens=None,
    )
    assert row_style(null_status) == "red"

    ok = fetch_row(str(seeded_db), 2)
    assert row_style(ok) == ""


def test_detail_text(seeded_db):
    from conductor.dashboard.data import RequestRow, fetch_row
    from conductor.dashboard.render import detail_text

    row = fetch_row(str(seeded_db), 4)
    text = detail_text(row)
    assert "request #4" in text
    assert "time" in text
    assert "harness" in text
    assert "tag" in text
    assert "rule" in text
    assert "escalated:truncated" in text
    assert "escalation retry" in text
    assert "requested model" in text
    assert "routed model" in text
    assert "stream" in text
    assert "status" in text
    assert "latency" in text
    assert "tokens" in text
    assert "cost" in text

    nullish = RequestRow(
        id=1,
        ts=time.time(),
        harness=None,
        tag=None,
        rule=None,
        requested_model=None,
        routed_model=None,
        input_tokens=None,
        output_tokens=None,
        cost_usd=None,
        latency_ms=None,
        stream=None,
        status=None,
        est_input_tokens=None,
    )
    t2 = detail_text(nullish)
    assert "—" in t2
    assert "?" in t2


def test_stats_text_empty_db(tmp_path):
    from conductor.dashboard.data import Summary
    from conductor.dashboard.render import stats_text

    # empty Summary (no rows)
    empty = Summary(
        by_model=[],
        by_rule=[],
        total_calls=0,
        total_cost=None,
        escalation_count=0,
        error_count=0,
    )
    text = stats_text(empty, days=7)
    assert "no requests yet" in text
    assert "by model" in text or "conductor stats" in text

    # also against a real empty ledger
    db = tmp_path / "empty.db"
    Ledger(db)
    from conductor.dashboard.data import fetch_summary

    s = fetch_summary(str(db), since_ts=0)
    text2 = stats_text(s, days=1)
    assert "no requests yet" in text2


# --- CLI ---


def test_parser_defaults():
    from conductor.dashboard.__main__ import build_parser

    p = build_parser()
    a = p.parse_args([])
    assert a.cmd == "live"
    assert a.interval == 1.0
    assert a.days == 1

    a2 = p.parse_args(["stats"])
    assert a2.cmd == "stats"
    assert a2.days == 7

    a3 = p.parse_args(["show", "5"])
    assert a3.cmd == "show"
    assert a3.id == 5


def test_parser_db_global_both_positions():
    """`--db` is global: valid before or after the subcommand (spec §3)."""
    from conductor.dashboard.__main__ import ROOT, build_parser

    p = build_parser()
    default_db = str(ROOT / "conductor.db")

    omitted = p.parse_args(["stats"])
    assert omitted.db == default_db

    before = p.parse_args(["--db", "/tmp/x.db", "stats"])
    assert before.db == "/tmp/x.db"

    after = p.parse_args(["stats", "--db", "/tmp/x.db"])
    assert after.db == "/tmp/x.db"


def test_parser_global_options_both_positions():
    """Pre-subcommand globals must not be overwritten by subparser defaults."""
    from conductor.dashboard.__main__ import build_parser

    p = build_parser()

    before = p.parse_args(["--interval", "5", "live"])
    assert before.interval == 5.0

    after = p.parse_args(["live", "--interval", "5"])
    assert after.interval == 5.0

    days_before = p.parse_args(["--days", "3", "live"])
    assert days_before.days == 3

    stats_default = p.parse_args(["stats"])
    assert stats_default.days == 7

    rows_default_cmd = p.parse_args(["--rows", "50"])
    assert rows_default_cmd.cmd == "live"
    assert rows_default_cmd.rows == 50


def test_main_show_missing_id_exit_code(seeded_db):
    from conductor.dashboard.__main__ import main

    assert main(["show", "9999", "--db", str(seeded_db)]) == 1


def test_main_stats_smoke(seeded_db, capsys):
    from conductor.dashboard.__main__ import main

    rc = main(["stats", "--db", str(seeded_db)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "claude-haiku-4-5" in out or "mystery-model" in out


# --- live smoke ---


def test_live_dashboard_tick(seeded_db, monkeypatch, tmp_path):
    from rich.console import Console

    from conductor.dashboard.app import LiveDashboard
    from conductor.dashboard.data import Health

    monkeypatch.setattr(
        "conductor.dashboard.data.fetch_health",
        lambda url, timeout=0.8: Health(
            up=True, default_model="claude-haiku-4-5", error=None
        ),
    )
    policy = tmp_path / "policy.yaml"
    policy.write_text(
        "escalation:\n  ladder: [claude-haiku-4-5, claude-sonnet-4-6]\n"
    )

    dash = LiveDashboard(
        db_path=str(seeded_db),
        proxy_url="http://localhost:8484",
        policy_path=str(policy),
        interval=1.0,
        days=1,
        rows=50,
    )
    dash.tick()
    layout = dash.render()
    console = Console(record=True, width=160, force_terminal=False)
    console.print(layout)
    text = console.export_text()
    assert text  # rendered something
    assert dash.after_id > 0


def test_live_dashboard_corrupt_db_degrades(tmp_path, monkeypatch):
    """Corrupt/unreadable ledger must not crash __init__ or tick()."""
    from conductor.dashboard.app import LiveDashboard
    from conductor.dashboard.data import Health

    monkeypatch.setattr(
        "conductor.dashboard.data.fetch_health",
        lambda url, timeout=0.8: Health(
            up=False, default_model=None, error="ConnectError"
        ),
    )
    bad = tmp_path / "corrupt.db"
    bad.write_bytes(b"not a sqlite database at all")
    policy = tmp_path / "policy.yaml"
    policy.write_text("escalation:\n  ladder: []\n")

    dash = LiveDashboard(
        db_path=str(bad),
        proxy_url="http://localhost:8484",
        policy_path=str(policy),
        interval=1.0,
        days=1,
        rows=50,
    )
    assert dash.warn == "db busy, retrying"
    dash.tick()
    assert dash.warn == "db busy, retrying"
