"""Real-data helpers for the Agents tab (detect, project, MCP, sessions).

No FastAPI imports — unit-testable against tmp dirs and a seeded ledger.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from conductor.dashboard.data import ROW_COLS, RequestRow, connect_ro, db_exists

AGENT_CATALOG: list[dict] = [
    {
        "id": "claude-code",
        "name": "Claude Code",
        "avatar": "CC",
        "accent": "#d97757",
        "cli_bin": "claude",
        "default_mode": "cli",
        "harness_prefixes": ["claude", "claude-cli", "claude-code"],
        "app_scheme": None,
    },
    {
        "id": "codex",
        "name": "Codex CLI",
        "avatar": "CX",
        "accent": "#79c0ff",
        "cli_bin": "codex",
        "default_mode": "cli",
        "harness_prefixes": ["codex"],
        "app_scheme": None,
    },
    {
        "id": "cursor",
        "name": "Cursor",
        "avatar": "CU",
        "accent": "#a371f7",
        "cli_bin": "cursor",
        "default_mode": "cli",
        "harness_prefixes": ["cursor"],
        "app_scheme": "cursor",
    },
    {
        "id": "opencode",
        "name": "OpenCode",
        "avatar": "OC",
        "accent": "#3fb950",
        "cli_bin": "opencode",
        "default_mode": "cli",
        "harness_prefixes": ["opencode"],
        "app_scheme": None,
    },
    {
        "id": "t3chat",
        "name": "T3 Chat",
        "avatar": "T3",
        "accent": "#e3b341",
        "cli_bin": None,
        "default_mode": "app",
        "harness_prefixes": ["t3"],
        "app_scheme": "https",
    },
    {
        "id": "openclaw",
        "name": "OpenClaw",
        "avatar": "OW",
        "accent": "#f47067",
        "cli_bin": "openclaw",
        "default_mode": "cli",
        "harness_prefixes": ["openclaw"],
        "app_scheme": None,
    },
    {
        "id": "hermes",
        "name": "Hermes",
        "avatar": "HM",
        "accent": "#56d4dd",
        "cli_bin": "hermes",
        "default_mode": "cli",
        "harness_prefixes": ["hermes"],
        "app_scheme": None,
    },
]

INTEGRATION_CATALOG: list[dict] = [
    {"id": "gdrive", "name": "Google Drive", "avatar": "GD", "accent": "#3fb950"},
    {"id": "github", "name": "GitHub", "avatar": "GH", "accent": "#8b949e"},
    {"id": "linear", "name": "Linear", "avatar": "LN", "accent": "#a371f7"},
    {"id": "plaid", "name": "Plaid", "avatar": "PL", "accent": "#58a6ff"},
]


@dataclass
class AgentInfo:
    id: str
    name: str
    avatar: str
    accent: str
    cli_bin: str | None
    installed: bool
    cli_path: str | None
    app_scheme: str | None
    app_open_url: str | None
    default_mode: str
    harness_prefixes: list[str]
    launch_command: str
    target_cli: str
    target_app: str
    routing_guaranteed: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ProjectInfo:
    path: str
    exists: bool
    name: str

    def to_dict(self) -> dict:
        return asdict(self)


def request_row_to_dict(row: RequestRow) -> dict:
    return {
        "id": row.id,
        "ts": row.ts,
        "harness": row.harness,
        "tag": row.tag,
        "rule": row.rule,
        "requested_model": row.requested_model,
        "routed_model": row.routed_model,
        "input_tokens": row.input_tokens,
        "output_tokens": row.output_tokens,
        "cost_usd": row.cost_usd,
        "latency_ms": row.latency_ms,
        "stream": row.stream,
        "status": row.status,
        "est_input_tokens": row.est_input_tokens,
        "escalated": row.escalated,
    }


@dataclass
class SessionPreview:
    agent_id: str
    mode: str
    routed_model: str | None
    rule: str | None
    rows: list[RequestRow] = field(default_factory=list)
    empty: bool = True

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "mode": self.mode,
            "routed_model": self.routed_model,
            "rule": self.rule,
            "rows": [request_row_to_dict(r) for r in self.rows],
            "empty": self.empty,
        }


def conductor_home(explicit: str | Path | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    return Path(os.environ.get("CONDUCTOR_HOME", "~/.conductor")).expanduser().resolve()


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text()) or {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def load_webui_prefs(home: Path | None = None) -> dict:
    return _read_json(conductor_home(home) / "webui.json")


def save_last_project(path: str, home: Path | None = None) -> None:
    root = conductor_home(home)
    prefs = load_webui_prefs(root)
    prefs["last_project"] = path
    _write_json(root / "webui.json", prefs)


def resolve_project(
    explicit: str | None = None,
    home: Path | None = None,
    cwd: str | None = None,
) -> ProjectInfo:
    """Resolution: explicit → CONDUCTOR_PROJECT → webui.json → git cwd → $HOME."""
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    env = os.environ.get("CONDUCTOR_PROJECT")
    if env:
        candidates.append(env)
    prefs = load_webui_prefs(home)
    if prefs.get("last_project"):
        candidates.append(str(prefs["last_project"]))
    work = Path(cwd or os.getcwd())
    if (work / ".git").exists():
        candidates.append(str(work))
    candidates.append(str(Path.home()))

    for raw in candidates:
        p = Path(raw).expanduser()
        try:
            resolved = p.resolve()
        except OSError:
            continue
        if resolved.is_dir():
            return ProjectInfo(path=str(resolved), exists=True, name=resolved.name)

    # Fallback: home even if somehow missing
    home_path = Path.home()
    return ProjectInfo(path=str(home_path), exists=home_path.is_dir(), name=home_path.name)


def set_project(path: str, home: Path | None = None) -> ProjectInfo:
    p = Path(path).expanduser()
    try:
        resolved = p.resolve()
    except OSError as e:
        raise ValueError(f"invalid path: {e}") from e
    if not resolved.is_dir():
        raise ValueError("path does not exist or is not a directory")
    info = ProjectInfo(path=str(resolved), exists=True, name=resolved.name)
    save_last_project(info.path, home)
    return info


def _cursor_installed(cli_path: str | None) -> bool:
    if cli_path:
        return True
    return Path("/Applications/Cursor.app").is_dir()


def _detect_cli(cli_bin: str | None) -> tuple[bool, str | None]:
    if not cli_bin:
        return False, None
    path = shutil.which(cli_bin)
    return (path is not None, path)


def build_launch_command(cli_bin: str, project_path: str, proxy_url: str) -> str:
    return (
        f"cd {project_path} && export ANTHROPIC_BASE_URL={proxy_url} && {cli_bin}"
    )


def _app_open_url(agent_id: str, scheme: str | None, project_path: str) -> str | None:
    if scheme == "cursor":
        return f"cursor://file/{project_path}"
    if agent_id == "t3chat":
        return "https://t3.chat/chat/last"
    return None


def list_agents(project_path: str, proxy_url: str) -> list[AgentInfo]:
    out: list[AgentInfo] = []
    for meta in AGENT_CATALOG:
        cli_bin = meta["cli_bin"]
        installed, cli_path = _detect_cli(cli_bin)
        if meta["id"] == "cursor":
            installed = _cursor_installed(cli_path)
        if meta["id"] == "t3chat":
            installed = True  # web app always "available"

        app_url = _app_open_url(meta["id"], meta.get("app_scheme"), project_path)
        routing_ok = meta["id"] != "t3chat"
        launch = ""
        if cli_bin:
            # cursor CLI uses `cursor .`
            bin_cmd = "cursor ." if meta["id"] == "cursor" else cli_bin
            launch = build_launch_command(bin_cmd, project_path, proxy_url)

        out.append(
            AgentInfo(
                id=meta["id"],
                name=meta["name"],
                avatar=meta["avatar"],
                accent=meta["accent"],
                cli_bin=cli_bin,
                installed=installed,
                cli_path=cli_path,
                app_scheme=meta.get("app_scheme"),
                app_open_url=app_url,
                default_mode=meta["default_mode"],
                harness_prefixes=list(meta["harness_prefixes"]),
                launch_command=launch,
                target_cli=f"$ cd {project_path}",
                target_app=(
                    "t3.chat/chat/last"
                    if meta["id"] == "t3chat"
                    else ("cursor://file/…" if app_url and meta["id"] == "cursor" else "—")
                ),
                routing_guaranteed=routing_ok,
            )
        )
    return out


def agent_by_id(agent_id: str) -> dict | None:
    for meta in AGENT_CATALOG:
        if meta["id"] == agent_id:
            return meta
    return None


def _row_from_tuple(t: tuple) -> RequestRow:
    return RequestRow(*t)


def fetch_agent_sessions(
    db_path: str,
    agent_id: str,
    mode: str = "cli",
    limit: int = 8,
    since_ts: float = 0.0,
) -> SessionPreview:
    meta = agent_by_id(agent_id)
    if meta is None:
        raise KeyError(agent_id)

    prefixes = [p.lower() for p in meta["harness_prefixes"]]
    empty = SessionPreview(
        agent_id=agent_id, mode=mode, routed_model=None, rule=None, rows=[], empty=True
    )
    if not db_exists(db_path) or not prefixes:
        return empty

    # Match harness containing any prefix (case-insensitive).
    clauses = " OR ".join(["lower(coalesce(harness,'')) LIKE ?" for _ in prefixes])
    like_params = [f"%{p}%" for p in prefixes]
    sql = (
        f"SELECT {ROW_COLS} FROM requests WHERE ts >= ? AND ({clauses}) "
        f"ORDER BY id DESC LIMIT ?"
    )
    with contextlib.closing(connect_ro(db_path)) as c:
        rows = c.execute(sql, (since_ts, *like_params, limit)).fetchall()

    typed = [_row_from_tuple(r) for r in reversed(rows)]  # ascending for display
    if not typed:
        return empty
    latest = typed[-1]
    return SessionPreview(
        agent_id=agent_id,
        mode=mode,
        routed_model=latest.routed_model,
        rule=latest.rule,
        rows=typed,
        empty=False,
    )


def detect_github() -> tuple[bool, str | None]:
    hosts = Path.home() / ".config" / "gh" / "hosts.yml"
    if hosts.is_file():
        try:
            text = hosts.read_text()
        except OSError:
            return False, None
        if "oauth_token" in text or "user:" in text:
            return True, str(hosts)
    if shutil.which("gh"):
        # Don't shell out in unit tests by default; file probe is enough for v0.4.
        # Presence of gh alone is not Connected — require hosts.yml.
        pass
    return False, None


def load_mcp(home: Path | None = None) -> dict:
    root = conductor_home(home)
    data = _read_json(root / "mcp.json")
    custom = data.get("custom") or []
    if not isinstance(custom, list):
        custom = []
    return {"custom": custom}


def save_mcp(custom: list[dict], home: Path | None = None) -> None:
    root = conductor_home(home)
    _write_json(root / "mcp.json", {"custom": custom})


def list_integrations() -> list[dict]:
    out = []
    for meta in INTEGRATION_CATALOG:
        connected = False
        source = None
        connectable = False
        if meta["id"] == "github":
            connected, source = detect_github()
        out.append(
            {
                **meta,
                "connected": connected,
                "connectable": connectable,
                "source": source,
            }
        )
    return out


def add_custom_mcp(name: str, url: str, home: Path | None = None) -> dict:
    name = name.strip()
    url = url.strip()
    if not name or not url:
        raise ValueError("name and url are required")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https", "stdio"):
        raise ValueError("url must start with http://, https://, or stdio://")
    entry = {"id": str(uuid.uuid4()), "name": name, "url": url}
    data = load_mcp(home)
    custom = list(data["custom"])
    custom.append(entry)
    save_mcp(custom, home)
    return entry


def remove_custom_mcp(mcp_id: str, home: Path | None = None) -> bool:
    data = load_mcp(home)
    custom = list(data["custom"])
    next_list = [m for m in custom if m.get("id") != mcp_id]
    if len(next_list) == len(custom):
        return False
    save_mcp(next_list, home)
    return True
