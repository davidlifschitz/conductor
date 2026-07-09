"""Tests for real-data Agents / MCP / project APIs."""

from __future__ import annotations

import json
import time
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from conductor.ledger import Ledger
from conductor.webui import agents_data
from conductor.webui.server import create_app


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "conductor-home"
    h.mkdir()
    return h


@pytest.fixture
def project_dir(tmp_path):
    p = tmp_path / "my-project"
    p.mkdir()
    (p / ".git").mkdir()
    return p


@pytest.fixture
def seeded_db(tmp_path):
    db = tmp_path / "test.db"
    ledger = Ledger(db)
    now = time.time()
    ledger.record(
        ts=now - 50,
        harness="claude-cli/1.2",
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
    ledger.record(
        ts=now - 10,
        harness="aider/0.60",
        tag=None,
        rule="default",
        requested_model="gpt-4o",
        routed_model="claude-haiku-4-5",
        input_tokens=50,
        output_tokens=10,
        cost_usd=0.0005,
        latency_ms=100,
        stream=0,
        status=200,
        est_input_tokens=50,
    )
    ledger.record(
        ts=now - 5,
        harness="claude-cli/1.2",
        tag=None,
        rule="planning-language",
        requested_model="claude-haiku-4-5",
        routed_model="claude-fable-5",
        input_tokens=200,
        output_tokens=40,
        cost_usd=0.01,
        latency_ms=900,
        stream=0,
        status=200,
        est_input_tokens=200,
    )
    return db


def test_detect_agents_marks_which_present(project_dir):
    def fake_which(name):
        return f"/usr/bin/{name}" if name in ("claude", "codex") else None

    with (
        mock.patch("conductor.webui.agents_data.shutil.which", side_effect=fake_which),
        mock.patch("conductor.webui.agents_data.Path.is_dir", return_value=False),
    ):
        agents = agents_data.list_agents(str(project_dir), "http://localhost:8484")

    by_id = {a.id: a for a in agents}
    assert by_id["claude-code"].installed is True
    assert by_id["codex"].installed is True
    assert by_id["cursor"].installed is False
    assert by_id["hermes"].installed is False
    assert by_id["t3chat"].installed is True  # web app


def test_launch_command_includes_proxy_and_project(project_dir):
    cmd = agents_data.build_launch_command(
        "claude", str(project_dir), "http://localhost:8484"
    )
    assert cmd.startswith(f"cd {project_dir}")
    assert "export ANTHROPIC_BASE_URL=http://localhost:8484" in cmd
    assert cmd.endswith("&& claude")


def test_project_put_rejects_missing_path(tmp_path, home, project_dir):
    app = create_app(
        str(tmp_path / "missing.db"),
        "http://localhost:8484",
        str(tmp_path / "policy.yaml"),
        project=str(project_dir),
        home=str(home),
    )
    client = TestClient(app)
    r = client.put("/api/project", json={"path": str(tmp_path / "nope")})
    assert r.status_code == 400


def test_project_put_persists(tmp_path, home, project_dir):
    other = tmp_path / "other"
    other.mkdir()
    app = create_app(
        str(tmp_path / "missing.db"),
        "http://localhost:8484",
        str(tmp_path / "policy.yaml"),
        project=str(project_dir),
        home=str(home),
    )
    client = TestClient(app)
    r = client.put("/api/project", json={"path": str(other)})
    assert r.status_code == 200
    assert r.json()["path"] == str(other.resolve())
    prefs = json.loads((home / "webui.json").read_text())
    assert prefs["last_project"] == str(other.resolve())


def test_mcp_custom_roundtrip(tmp_path, home, project_dir):
    app = create_app(
        str(tmp_path / "missing.db"),
        "http://localhost:8484",
        str(tmp_path / "policy.yaml"),
        project=str(project_dir),
        home=str(home),
    )
    client = TestClient(app)
    r = client.post("/api/mcp/custom", json={"name": "Notion", "url": "https://mcp.example/sse"})
    assert r.status_code == 200
    entry = r.json()
    assert entry["name"] == "Notion"
    assert entry["id"]

    listed = client.get("/api/mcp").json()
    assert any(m["id"] == entry["id"] for m in listed["custom"])

    d = client.delete(f"/api/mcp/custom/{entry['id']}")
    assert d.status_code == 200
    listed2 = client.get("/api/mcp").json()
    assert not any(m["id"] == entry["id"] for m in listed2["custom"])


def test_sessions_filters_by_harness(seeded_db, home, project_dir):
    preview = agents_data.fetch_agent_sessions(str(seeded_db), "claude-code", limit=8)
    assert preview.empty is False
    assert len(preview.rows) == 2
    assert all("claude" in (r.harness or "").lower() for r in preview.rows)
    assert preview.routed_model == "claude-fable-5"


def test_sessions_empty(seeded_db):
    preview = agents_data.fetch_agent_sessions(str(seeded_db), "hermes", limit=8)
    assert preview.empty is True
    assert preview.rows == []


def test_api_agents_and_sessions(seeded_db, home, project_dir, tmp_path):
    policy = tmp_path / "policy.yaml"
    policy.write_text("escalation:\n  ladder: [a, b]\n")
    app = create_app(
        str(seeded_db),
        "http://localhost:8484",
        str(policy),
        project=str(project_dir),
        home=str(home),
    )
    client = TestClient(app)
    agents = client.get("/api/agents").json()
    assert agents["project"]["name"] == "my-project"
    assert len(agents["agents"]) == 7

    sessions = client.get("/api/agents/claude-code/sessions").json()
    assert sessions["empty"] is False
    assert len(sessions["rows"]) == 2

    missing = client.get("/api/agents/nope/sessions")
    assert missing.status_code == 404


def test_integrations_never_fake_connected(home, project_dir, tmp_path):
    app = create_app(
        str(tmp_path / "x.db"),
        "http://localhost:8484",
        str(tmp_path / "p.yaml"),
        project=str(project_dir),
        home=str(home),
    )
    client = TestClient(app)
    mcp = client.get("/api/mcp").json()
    for ig in mcp["integrations"]:
        if ig["id"] != "github":
            assert ig["connected"] is False
        assert ig["connectable"] is False
