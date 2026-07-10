"""Unified local-app CLI: `conductor status|start|stop|open|setup|…`."""

from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from pathlib import Path

from conductor import local
from conductor.serve import ensure_home


def _print_status(st: dict) -> None:
    print(f"home:       {st['home']}")
    print(f"env file:   {st['env_file']}" + (" (ok)" if st["env_file_exists"] else " (missing)"))
    keys = st["keys"]
    key_bits = []
    for name, present in keys.items():
        key_bits.append(f"{name}={'set' if present else 'missing'}")
    print(f"keys:       {', '.join(key_bits)}")
    for name in ("proxy", "dashboard"):
        s = st[name]
        state = "UP" if s["listening"] else "down"
        pid = s["pid"] or "—"
        print(f"{name + ':':12}{state}  :{s['port']}  pid={pid}")
    la = st["launchagent"]
    if sys.platform == "darwin":
        flag = "loaded" if la["loaded"] else ("installed" if la["installed"] else "not installed")
        print(f"login item: {flag}  ({la['label']})")


def cmd_status(_args: argparse.Namespace) -> int:
    local.apply_env()
    _print_status(local.status())
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    home = ensure_home(args.home)
    local.ensure_env_file(home)
    local.apply_env(home)

    if not os.environ.get("OPENROUTER_API_KEY") and not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "warning: no OPENROUTER_API_KEY or ANTHROPIC_API_KEY set — "
            f"edit {local.env_file(home)}",
            file=sys.stderr,
        )

    pid, already = local.start_proxy(home)
    if already:
        print(f"proxy already running on :{local.PROXY_PORT}")
    else:
        print(f"starting proxy (pid {pid})…")
        if not local.wait_port(local.PROXY_PORT):
            print("proxy failed to bind — see ~/.conductor/logs/proxy.log", file=sys.stderr)
            return 1
        print(f"proxy UP  http://127.0.0.1:{local.PROXY_PORT}")

    if not args.proxy_only:
        project = args.project or os.environ.get("CONDUCTOR_PROJECT")
        dpid, dalready = local.start_dashboard(home, project=project)
        if dalready:
            print(f"dashboard already running on :{local.DASH_PORT}")
        else:
            print(f"starting dashboard (pid {dpid})…")
            if not local.wait_port(local.DASH_PORT):
                print(
                    "dashboard failed to bind — see ~/.conductor/logs/dashboard.log",
                    file=sys.stderr,
                )
                return 1
            print(f"dashboard UP  http://127.0.0.1:{local.DASH_PORT}")

    if args.open:
        webbrowser.open(f"http://127.0.0.1:{local.DASH_PORT}")
    return 0


def cmd_stop(_args: argparse.Namespace) -> int:
    results = local.stop_all()
    for name, stopped in results.items():
        print(f"{name}: {'stopped' if stopped else 'not running'}")
    # Also free ports if something else was bound without a pidfile
    for port, label in ((local.PROXY_PORT, "proxy"), (local.DASH_PORT, "dashboard")):
        if local.port_listening(port) and not results.get(label):
            print(
                f"warning: :{port} still listening (not managed by conductor pidfile)",
                file=sys.stderr,
            )
    return 0


def cmd_open(_args: argparse.Namespace) -> int:
    if not local.port_listening(local.DASH_PORT):
        print("dashboard not running — try: conductor start", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{local.DASH_PORT}"
    webbrowser.open(url)
    print(url)
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    home = ensure_home(args.home)
    env_path = local.ensure_env_file(home)
    ctx = home / "context"
    ctx.mkdir(parents=True, exist_ok=True)
    for name, body in (
        (
            "SKILL.md",
            "# Skill\n\n- Prefer concise answers; skip restating the question.\n"
            "- When editing code, follow the existing style of the file.\n",
        ),
        (
            "REPORT.md",
            "# Report\n\n- Active project: (edit me)\n",
        ),
    ):
        p = ctx / name
        if not p.exists():
            p.write_text(body)
            print(f"created {p}")

    print(f"home:     {home}")
    print(f"env:      {env_path}")
    print(f"policy:   {home / 'policy.yaml'}")
    print(f"pricing:  {home / 'pricing.yaml'}")
    print()
    print("Next:")
    print(f"  1. Edit keys if needed:  {env_path}")
    print("  2. Start:                 conductor start --open")
    print("  3. Route a shell:         conductor-on   # (from your ~/.zshrc helpers)")
    if sys.platform == "darwin":
        print("  4. Optional login start:  conductor service install")
    return 0


def cmd_service_install(_args: argparse.Namespace) -> int:
    try:
        plist = local.install_launchagent()
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"installed {plist}")
    print("proxy will start at login (KeepAlive). Check: conductor status")
    if local.wait_port(local.PROXY_PORT, timeout=6):
        print(f"proxy UP  http://127.0.0.1:{local.PROXY_PORT}")
    else:
        print("proxy not up yet — check ~/Library/Logs or ~/.conductor/logs/")
    return 0


def cmd_service_uninstall(_args: argparse.Namespace) -> int:
    if local.uninstall_launchagent():
        print("LaunchAgent removed")
    else:
        print("no LaunchAgent installed")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="conductor",
        description="Conductor local app — start/stop the proxy + dashboard.",
    )
    ap.add_argument("--home", help="override CONDUCTOR_HOME")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="show proxy/dashboard/key status")
    sub.add_parser("stop", help="stop proxy and dashboard")
    sub.add_parser("open", help="open the web dashboard in a browser")
    sub.add_parser("setup", help="scaffold ~/.conductor (env, context, configs)")

    start = sub.add_parser("start", help="start proxy (+ dashboard)")
    start.add_argument("--proxy-only", action="store_true", help="skip the web dashboard")
    start.add_argument("--open", action="store_true", help="open dashboard in browser")
    start.add_argument("--project", help="project dir for Agents launch commands")

    svc = sub.add_parser("service", help="macOS login-item (LaunchAgent) for the proxy")
    svc_sub = svc.add_subparsers(dest="service_cmd", required=True)
    svc_sub.add_parser("install", help="install + load LaunchAgent (start at login)")
    svc_sub.add_parser("uninstall", help="unload + remove LaunchAgent")

    return ap


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.home:
        os.environ["CONDUCTOR_HOME"] = str(Path(args.home).expanduser())

    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd == "start":
        return cmd_start(args)
    if args.cmd == "stop":
        return cmd_stop(args)
    if args.cmd == "open":
        return cmd_open(args)
    if args.cmd == "setup":
        return cmd_setup(args)
    if args.cmd == "service":
        if args.service_cmd == "install":
            return cmd_service_install(args)
        if args.service_cmd == "uninstall":
            return cmd_service_uninstall(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
