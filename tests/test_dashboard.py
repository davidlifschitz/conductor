"""Tests for conductor.dashboard — data, render, CLI, live smoke."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

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
