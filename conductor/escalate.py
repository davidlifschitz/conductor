"""Auto-escalation logic (pure — no I/O; the proxy performs the retry).

A response is judged too weak for its tier when it trips one of the
heuristics below. The proxy then re-issues the same request on the next
model in the escalation ladder and logs both calls.
"""

import re

UNCERTAINTY_MARKERS = (
    "i'm not sure", "i am not sure", "i can't determine", "i cannot determine",
    "it's unclear", "i don't have enough", "i may be wrong", "hard to say",
)


def _text_of(response_json: dict) -> str:
    parts = response_json.get("content") or []
    return " ".join(b.get("text", "") for b in parts if isinstance(b, dict) and b.get("type") == "text")


def should_escalate(response_json: dict, cfg: dict) -> str | None:
    """Return a reason string if this response warrants a retry up-tier, else None."""
    if not cfg.get("enabled", False):
        return None

    text = _text_of(response_json).strip()
    stop = response_json.get("stop_reason")
    out_tokens = (response_json.get("usage") or {}).get("output_tokens") or 0

    if not text and stop != "tool_use":
        return "empty-content"

    if stop == "refusal":
        return "refusal-stop"

    # Hit max_tokens almost immediately -> model likely floundered, not truncated.
    if stop == "max_tokens" and out_tokens < int(cfg.get("min_truncation_tokens", 64)):
        return "instant-truncation"

    lowered = text.lower()
    hits = sum(lowered.count(m) for m in UNCERTAINTY_MARKERS)
    words = max(len(re.findall(r"\w+", text)), 1)
    if hits >= int(cfg.get("uncertainty_min_hits", 2)) and words < int(cfg.get("uncertainty_max_words", 250)):
        return "high-uncertainty-density"

    return None


def next_tier(model: str, ladder: list[str]) -> str | None:
    """The model one rung above `model` in the ladder, or None at the top / off-ladder."""
    try:
        i = ladder.index(model)
    except ValueError:
        return None
    return ladder[i + 1] if i + 1 < len(ladder) else None
