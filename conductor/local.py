"""Local app control: process lifecycle, env loading, macOS LaunchAgent."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

PROXY_PORT = 8484
DASH_PORT = 8485
LABEL = "com.conductor.proxy"


def home_dir(explicit: str | Path | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    return Path(os.environ.get("CONDUCTOR_HOME", "~/.conductor")).expanduser().resolve()


def run_dir(home: Path | None = None) -> Path:
    d = home_dir(home) / "run"
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_dir(home: Path | None = None) -> Path:
    d = home_dir(home) / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def env_file(home: Path | None = None) -> Path:
    return home_dir(home) / "env"


def load_env_file(path: Path | None = None) -> dict[str, str]:
    """Parse KEY=VALUE lines (no export, no shell). Does not override existing env."""
    p = path or env_file()
    out: dict[str, str] = {}
    if not p.is_file():
        return out
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        if key:
            out[key] = val
    return out


def apply_env(home: Path | None = None) -> dict[str, str]:
    """Load ~/.conductor/env into os.environ (existing keys win)."""
    loaded = load_env_file(env_file(home))
    for k, v in loaded.items():
        os.environ.setdefault(k, v)
    os.environ.setdefault("CONDUCTOR_HOME", str(home_dir(home)))
    return loaded


def ensure_env_file(home: Path | None = None) -> Path:
    """Create ~/.conductor/env from Hermes OpenRouter key if missing."""
    from conductor.serve import ensure_home

    root = ensure_home(str(home_dir(home)) if home else None)
    path = env_file(root)
    if path.is_file():
        return path

    lines = [
        "# Conductor local env — loaded by `conductor start` and the LaunchAgent.",
        "# Existing shell exports take precedence over these values.",
        "",
    ]
    hermes = Path.home() / ".hermes" / ".env"
    or_key = ""
    if hermes.is_file():
        for raw in hermes.read_text().splitlines():
            if raw.startswith("OPENROUTER_API_KEY="):
                or_key = raw.split("=", 1)[1].strip().strip("'").strip('"')
                break
    if or_key:
        lines.append(f"OPENROUTER_API_KEY={or_key}")
    else:
        lines.append("# OPENROUTER_API_KEY=sk-or-...")
    lines.append("# ANTHROPIC_API_KEY=sk-ant-...")
    lines.append("")
    path.write_text("\n".join(lines))
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)
    return path


def _pid_path(name: str, home: Path | None = None) -> Path:
    return run_dir(home) / f"{name}.pid"


def read_pid(name: str, home: Path | None = None) -> int | None:
    p = _pid_path(name, home)
    if not p.is_file():
        return None
    try:
        pid = int(p.read_text().strip())
    except (OSError, ValueError):
        return None
    if not _pid_alive(pid):
        p.unlink(missing_ok=True)
        return None
    return pid


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def port_listening(port: int, host: str = "127.0.0.1") -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) == 0


def _which_self() -> str:
    """Path to the `conductor` console script, or python -m fallback."""
    found = shutil_which("conductor")
    if found:
        return found
    return f"{sys.executable} -m conductor.cli"


def shutil_which(cmd: str) -> str | None:
    import shutil

    return shutil.which(cmd)


def start_process(
    name: str,
    argv: list[str],
    home: Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> int:
    """Spawn a detached process; write pid file. Returns pid."""
    apply_env(home)
    root = home_dir(home)
    log = log_dir(root) / f"{name}.log"
    pid_file = _pid_path(name, root)

    existing = read_pid(name, root)
    if existing:
        return existing

    env = os.environ.copy()
    env["CONDUCTOR_HOME"] = str(root)
    if extra_env:
        env.update(extra_env)

    log_f = open(log, "a")  # noqa: SIM115 — kept open for the child lifetime
    proc = subprocess.Popen(
        argv,
        stdout=log_f,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        env=env,
        cwd=str(root),
    )
    pid_file.write_text(str(proc.pid))
    return proc.pid


def stop_process(name: str, home: Path | None = None, timeout: float = 5.0) -> bool:
    pid = read_pid(name, home)
    pid_file = _pid_path(name, home)
    if pid is None:
        pid_file.unlink(missing_ok=True)
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pid_file.unlink(missing_ok=True)
        return False
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _pid_alive(pid):
            break
        time.sleep(0.1)
    if _pid_alive(pid):
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)
    pid_file.unlink(missing_ok=True)
    return True


def proxy_argv() -> list[str]:
    bin_path = shutil_which("conductor-proxy")
    if bin_path:
        return [bin_path]
    return [sys.executable, "-m", "conductor.serve"]


def dashboard_argv(project: str | None = None) -> list[str]:
    bin_path = shutil_which("conductor-dashboard")
    args = [bin_path] if bin_path else [sys.executable, "-m", "conductor.dashboard"]
    args += ["web", "--host", "127.0.0.1", "--port", str(DASH_PORT)]
    if project:
        args += ["--project", project]
    return args


def start_proxy(home: Path | None = None) -> tuple[int, bool]:
    """Returns (pid, already_running)."""
    if port_listening(PROXY_PORT):
        pid = read_pid("proxy", home) or 0
        return pid, True
    pid = start_process("proxy", proxy_argv(), home)
    return pid, False


def start_dashboard(home: Path | None = None, project: str | None = None) -> tuple[int, bool]:
    if port_listening(DASH_PORT):
        pid = read_pid("dashboard", home) or 0
        return pid, True
    pid = start_process("dashboard", dashboard_argv(project), home)
    return pid, False


def stop_all(home: Path | None = None) -> dict[str, bool]:
    return {
        "proxy": stop_process("proxy", home),
        "dashboard": stop_process("dashboard", home),
    }


def wait_port(port: int, timeout: float = 8.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_listening(port):
            return True
        time.sleep(0.15)
    return False


def status(home: Path | None = None) -> dict:
    apply_env(home)
    root = home_dir(home)
    keys = {
        "OPENROUTER_API_KEY": bool(os.environ.get("OPENROUTER_API_KEY")),
        "ANTHROPIC_API_KEY": bool(os.environ.get("ANTHROPIC_API_KEY")),
    }
    return {
        "home": str(root),
        "env_file": str(env_file(root)),
        "env_file_exists": env_file(root).is_file(),
        "keys": keys,
        "proxy": {
            "port": PROXY_PORT,
            "listening": port_listening(PROXY_PORT),
            "pid": read_pid("proxy", root),
        },
        "dashboard": {
            "port": DASH_PORT,
            "listening": port_listening(DASH_PORT),
            "pid": read_pid("dashboard", root),
        },
        "launchagent": {
            "label": LABEL,
            "installed": launchagent_plist().is_file(),
            "loaded": launchagent_loaded(),
        },
    }


# ---- LaunchAgent (macOS) -------------------------------------------------


def launchagent_plist() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def launchagent_loaded() -> bool:
    if sys.platform != "darwin":
        return False
    try:
        r = subprocess.run(
            ["launchctl", "list", LABEL],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def render_launchagent_plist(program_args: list[str], home: Path, env: dict[str, str]) -> str:
    def esc(s: str) -> str:
        return (
            s.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    args_xml = "\n".join(f"    <string>{esc(a)}</string>" for a in program_args)
    env_xml = "\n".join(
        f"    <key>{esc(k)}</key>\n    <string>{esc(v)}</string>" for k, v in sorted(env.items())
    )
    log = home / "logs" / "proxy.launchd.log"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array>
{args_xml}
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>WorkingDirectory</key>
  <string>{esc(str(home))}</string>
  <key>StandardOutPath</key>
  <string>{esc(str(log))}</string>
  <key>StandardErrorPath</key>
  <string>{esc(str(log))}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>CONDUCTOR_HOME</key>
    <string>{esc(str(home))}</string>
{env_xml}
  </dict>
</dict>
</plist>
"""


def install_launchagent(home: Path | None = None) -> Path:
    if sys.platform != "darwin":
        raise RuntimeError("LaunchAgent is only supported on macOS")
    apply_env(home)
    root = home_dir(home)
    ensure_env_file(root)
    log_dir(root)
    program = proxy_argv()
    env = load_env_file(env_file(root))
    # Don't put empty keys in the plist
    env = {k: v for k, v in env.items() if v}
    plist = launchagent_plist()
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_text(render_launchagent_plist(program, root, env))
    # Reload
    subprocess.run(["launchctl", "unload", str(plist)], capture_output=True, check=False)
    r = subprocess.run(["launchctl", "load", str(plist)], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or r.stdout.strip() or "launchctl load failed")
    return plist


def uninstall_launchagent() -> bool:
    if sys.platform != "darwin":
        return False
    plist = launchagent_plist()
    if plist.is_file():
        subprocess.run(["launchctl", "unload", str(plist)], capture_output=True, check=False)
        plist.unlink(missing_ok=True)
        return True
    return False
