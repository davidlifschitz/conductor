"""OpenAI-compatible ingress: pure translation between /v1/chat/completions
and Anthropic /v1/messages shapes. Text, system prompts, and multi-turn are
covered; tool-call translation is deliberately out of scope for v0.2.
"""

import json
import time
import uuid

STOP_MAP = {"end_turn": "stop", "max_tokens": "length", "stop_sequence": "stop", "refusal": "content_filter"}


def to_anthropic(body: dict) -> dict:
    """OpenAI chat.completions request -> Anthropic messages request."""
    system_parts, messages = [], []
    for m in body.get("messages", []):
        role, content = m.get("role"), m.get("content")
        if isinstance(content, list):  # OpenAI content-part form
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
        if role in ("system", "developer"):
            system_parts.append(content or "")
        elif role in ("user", "assistant"):
            messages.append({"role": role, "content": content or ""})

    out = {
        "model": body.get("model", ""),
        "messages": messages,
        "max_tokens": body.get("max_tokens") or body.get("max_completion_tokens") or 4096,
    }
    if system_parts:
        out["system"] = "\n\n".join(system_parts)
    for k_src, k_dst in (("temperature", "temperature"), ("top_p", "top_p"), ("stop", "stop_sequences")):
        if body.get(k_src) is not None:
            v = body[k_src]
            out[k_dst] = [v] if k_dst == "stop_sequences" and isinstance(v, str) else v
    if body.get("stream"):
        out["stream"] = True
    return out


def from_anthropic(resp: dict) -> dict:
    """Anthropic messages response -> OpenAI chat.completions response."""
    text = "".join(b.get("text", "") for b in resp.get("content", [])
                   if isinstance(b, dict) and b.get("type") == "text")
    usage = resp.get("usage") or {}
    in_tok, out_tok = usage.get("input_tokens", 0), usage.get("output_tokens", 0)
    return {
        "id": "chatcmpl-" + resp.get("id", uuid.uuid4().hex),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": resp.get("model", ""),
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": STOP_MAP.get(resp.get("stop_reason"), "stop"),
        }],
        "usage": {"prompt_tokens": in_tok, "completion_tokens": out_tok, "total_tokens": in_tok + out_tok},
    }


def stream_translate(sse_lines, model: str):
    """Generator: Anthropic SSE lines (bytes) -> OpenAI chunk SSE lines (bytes)."""
    chunk_id = "chatcmpl-" + uuid.uuid4().hex
    created = int(time.time())

    def chunk(delta: dict, finish=None) -> bytes:
        payload = {
            "id": chunk_id, "object": "chat.completion.chunk", "created": created, "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        return b"data: " + json.dumps(payload).encode() + b"\n\n"

    yield chunk({"role": "assistant", "content": ""})
    for raw in sse_lines:
        line = raw.strip()
        if not line.startswith(b"data:"):
            continue
        try:
            evt = json.loads(line[5:].strip())
        except (ValueError, UnicodeDecodeError):
            continue
        etype = evt.get("type")
        if etype == "content_block_delta":
            text = (evt.get("delta") or {}).get("text")
            if text:
                yield chunk({"content": text})
        elif etype == "message_delta":
            stop = (evt.get("delta") or {}).get("stop_reason")
            if stop:
                yield chunk({}, finish=STOP_MAP.get(stop, "stop"))
    yield b"data: [DONE]\n\n"
