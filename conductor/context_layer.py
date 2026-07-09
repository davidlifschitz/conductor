"""Context portability: inject a SKILL.md / REPORT.md pair into every request.

Stable reasoning process lives in SKILL.md, volatile facts in REPORT.md
(the SKILL/REPORT split). Conductor prepends both as a synthetic system
block so the same memory rides along regardless of which harness or model
is on the other end.

Opt-out per request with the [conductor:nocontext] in-band tag.
"""

import time
from pathlib import Path

_CACHE: dict[str, tuple[float, float, str]] = {}  # path -> (mtime, read_at, text)
_TTL = 5.0  # seconds between mtime checks

BLOCK_HEADER = "## Portable context (injected by Conductor)"


def _read_cached(path: Path) -> str:
    key = str(path)
    now = time.time()
    cached = _CACHE.get(key)
    if cached and now - cached[1] < _TTL:
        return cached[2]
    try:
        mtime = path.stat().st_mtime
    except OSError:
        _CACHE[key] = (0.0, now, "")
        return ""
    if cached and cached[0] == mtime:
        _CACHE[key] = (mtime, now, cached[2])
        return cached[2]
    text = path.read_text(errors="replace")
    _CACHE[key] = (mtime, now, text)
    return text


def _assemble(cfg: dict) -> str:
    root = Path(cfg.get("dir", "~/.conductor/context")).expanduser()
    sections = []
    for fname, label in (("SKILL.md", "Skill"), ("REPORT.md", "Report")):
        body = _read_cached(root / fname).strip()
        if body:
            sections.append(f"### {label} ({fname})\n{body}")
    if not sections:
        return ""
    return BLOCK_HEADER + "\n\n" + "\n\n".join(sections)


def inject(system, cfg: dict):
    """Return a new `system` value with the portable context prepended.

    Accepts either the string form or the content-block list form and
    preserves whichever the caller used. Never mutates the input.
    """
    if not cfg.get("enabled", False):
        return system

    # In-band opt-out
    probe = system if isinstance(system, str) else " ".join(
        b.get("text", "") for b in (system or []) if isinstance(b, dict))
    if "[conductor:nocontext]" in (probe or ""):
        return system

    block = _assemble(cfg)
    if not block:
        return system

    if system is None or isinstance(system, str):
        return block + ("\n\n" + system if system else "")
    return [{"type": "text", "text": block}, *system]
