"""Installed entry point for the proxy: `conductor-proxy`.

When Conductor is installed as a package (rather than run from a clone),
there is no repo root to read policy.yaml from. This launcher defaults
CONDUCTOR_HOME to ~/.conductor, scaffolds it with the bundled default
policy.yaml / pricing.yaml on first run, then starts uvicorn.
"""

import argparse
import os
import shutil
from pathlib import Path

DEFAULTS = Path(__file__).resolve().parent / "defaults"


def ensure_home(path: str | None = None) -> Path:
    home = Path(path or os.environ.get("CONDUCTOR_HOME", "~/.conductor")).expanduser()
    home.mkdir(parents=True, exist_ok=True)
    for fname in ("policy.yaml", "pricing.yaml"):
        target = home / fname
        if not target.exists():
            shutil.copy(DEFAULTS / fname, target)
            print(f"created {target} (edit to taste)")
    return home


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="conductor-proxy",
        description="Run the Conductor routing proxy.",
    )
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8484)
    ap.add_argument("--home", help="config/ledger dir (default: $CONDUCTOR_HOME or ~/.conductor)")
    args = ap.parse_args()

    home = ensure_home(args.home)
    # proxy.py resolves policy.yaml / pricing.yaml / conductor.db from this.
    os.environ["CONDUCTOR_HOME"] = str(home)

    import uvicorn

    uvicorn.run("conductor.proxy:app", host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
