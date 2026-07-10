"""Tests for local app control (no LaunchAgent load in CI)."""

from __future__ import annotations

from pathlib import Path

from conductor import local


def test_load_env_file(tmp_path: Path):
    p = tmp_path / "env"
    p.write_text("# comment\nOPENROUTER_API_KEY=sk-test\nANTHROPIC_API_KEY=\nFOO=bar\n")
    data = local.load_env_file(p)
    assert data["OPENROUTER_API_KEY"] == "sk-test"
    assert data["FOO"] == "bar"
    assert data["ANTHROPIC_API_KEY"] == ""


def test_ensure_env_file_creates(tmp_path: Path, monkeypatch):
    home = tmp_path / "c-home"
    home.mkdir()
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))

    hermes_dir = tmp_path / ".hermes"
    hermes_dir.mkdir()
    (hermes_dir / ".env").write_text('OPENROUTER_API_KEY="sk-from-hermes"\n')
    monkeypatch.setattr(local.Path, "home", staticmethod(lambda: tmp_path))

    from conductor.serve import ensure_home

    ensure_home(str(home))
    path = local.ensure_env_file(home)
    assert path.is_file()
    assert "OPENROUTER_API_KEY=sk-from-hermes" in path.read_text()
    path.write_text("OPENROUTER_API_KEY=keep-me\n")
    local.ensure_env_file(home)
    assert "keep-me" in path.read_text()


def test_render_launchagent_plist_escapes():
    xml = local.render_launchagent_plist(
        ["/usr/bin/conductor-proxy"],
        Path("/tmp/home"),
        {"OPENROUTER_API_KEY": "sk&x"},
    )
    assert "<string>/usr/bin/conductor-proxy</string>" in xml
    assert "sk&amp;x" in xml
    assert local.LABEL in xml


def test_port_listening_closed():
    # Pick a port that should be closed
    assert local.port_listening(1) is False


def test_cli_parser_start_flags():
    from conductor.cli import build_parser

    p = build_parser()
    args = p.parse_args(["start", "--open", "--proxy-only"])
    assert args.cmd == "start"
    assert args.open is True
    assert args.proxy_only is True
    args2 = p.parse_args(["service", "install"])
    assert args2.cmd == "service"
    assert args2.service_cmd == "install"
