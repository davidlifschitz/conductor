"""Conductor proxy — composition root.

Feature modules (escalate, context_layer, openai_compat) are pure logic;
all HTTP and logging happens here. Ingress:

  /v1/messages          — Anthropic Messages API (Claude Code et al.)
  /v1/chat/completions  — OpenAI-compatible (anything else)
"""

import json
import os
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from . import context_layer, escalate, openai_compat
from .ledger import Ledger, Pricing
from .policy import Policy

ROOT = Path(os.environ.get("CONDUCTOR_HOME", Path(__file__).resolve().parents[1]))
policy = Policy(ROOT / "policy.yaml")
pricing = Pricing(ROOT / "pricing.yaml")
ledger = Ledger(ROOT / "conductor.db")

app = FastAPI(title="conductor")
HOP_HEADERS = {"host", "content-length", "authorization", "x-api-key", "x-conductor-tag"}


def _headers_for(req: Request, api_key: str) -> dict:
    headers = {k: v for k, v in req.headers.items() if k.lower() not in HOP_HEADERS}
    headers["x-api-key"] = api_key
    headers.setdefault("anthropic-version", "2023-06-01")
    headers["content-type"] = "application/json"
    return headers


def _log(base, usage, status, started, rule=None):
    in_tok, out_tok = usage.get("input_tokens"), usage.get("output_tokens")
    row = dict(base)
    if rule:
        row["rule"] = rule
    ledger.record(**row, input_tokens=in_tok, output_tokens=out_tok,
                  cost_usd=pricing.cost(row["routed_model"], in_tok, out_tok),
                  latency_ms=int((time.time() - started) * 1000), status=status)


async def _call(url, headers, body):
    async with httpx.AsyncClient(timeout=600) as client:
        return await client.post(url, headers=headers, json=body)


async def _dispatch(req: Request, body: dict):
    """Route, inject context, forward. Returns (upstream_json|stream_ctx, base_row)."""
    body["system"] = context_layer.inject(body.get("system"), policy.context_cfg)
    decision = policy.decide(body, dict(req.headers))
    requested = body.get("model", "")
    body["model"] = decision.routed_model
    provider = policy.provider_for(decision.routed_model)
    base = {
        "harness": req.headers.get("user-agent", "")[:120],
        "tag": decision.tag, "rule": decision.rule_name,
        "requested_model": requested, "routed_model": decision.routed_model,
        "est_input_tokens": decision.est_input_tokens,
        "stream": 1 if body.get("stream") else 0,
    }
    url = f"{provider['base_url']}/v1/messages"
    return url, _headers_for(req, provider["api_key"]), base


async def _run_with_escalation(url, headers, body, base, started):
    """Non-streaming call + up-tier retry when the response looks weak."""
    upstream = await _call(url, headers, body)
    if upstream.status_code != 200:
        _log(base, {}, upstream.status_code, started)
        return upstream

    resp = upstream.json()
    _log(base, resp.get("usage", {}), 200, started)

    cfg = policy.escalation_cfg
    reason = escalate.should_escalate(resp, cfg)
    up = escalate.next_tier(base["routed_model"], cfg.get("ladder", [])) if reason else None
    if not up:
        return upstream

    retry_body = dict(body, model=up)
    provider = policy.provider_for(up)
    retry_headers = dict(headers, **{"x-api-key": provider["api_key"]})
    retry_base = dict(base, routed_model=up)
    t2 = time.time()
    second = await _call(f"{provider['base_url']}/v1/messages", retry_headers, retry_body)
    usage = second.json().get("usage", {}) if second.status_code == 200 else {}
    _log(retry_base, usage, second.status_code, t2, rule=f"escalated:{reason}")
    return second if second.status_code == 200 else upstream


def _stream_passthrough(url, headers, body, base, started, translate_model=None):
    usage: dict = {}

    async def gen():
        status, buf = 0, b""
        lines = []
        try:
            async with httpx.AsyncClient(timeout=600) as client:
                async with client.stream("POST", url, headers=headers, json=body) as upstream:
                    status = upstream.status_code
                    async for chunk in upstream.aiter_bytes():
                        buf += chunk
                        while b"\n" in buf:
                            line, buf = buf.split(b"\n", 1)
                            _harvest_usage(line, usage)
                            lines.append(line)
                        if translate_model is None:
                            yield chunk
                        else:
                            for out in openai_compat.stream_translate(_drain(lines), translate_model):
                                yield out
            if translate_model is not None and lines:  # flush any tail
                for out in openai_compat.stream_translate(_drain(lines), translate_model):
                    yield out
        finally:
            _log(base, usage, status, started)

    return StreamingResponse(gen(), media_type="text/event-stream")


def _drain(lines: list) -> list:
    out, lines[:] = lines[:], []
    return out


def _harvest_usage(line: bytes, usage: dict) -> None:
    if not line.startswith(b"data:"):
        return
    try:
        evt = json.loads(line[5:].strip())
    except (ValueError, UnicodeDecodeError):
        return
    if evt.get("type") == "message_start":
        usage["input_tokens"] = evt.get("message", {}).get("usage", {}).get("input_tokens")
    elif evt.get("type") == "message_delta":
        out = evt.get("usage", {}).get("output_tokens")
        if out is not None:
            usage["output_tokens"] = out


@app.post("/v1/messages")
async def messages(req: Request):
    body = await req.json()
    url, headers, base = await _dispatch(req, body)
    started = time.time()
    if body.get("stream"):
        return _stream_passthrough(url, headers, body, base, started)
    upstream = await _run_with_escalation(url, headers, body, base, started)
    return Response(content=upstream.content, status_code=upstream.status_code,
                    media_type=upstream.headers.get("content-type", "application/json"))


@app.post("/v1/chat/completions")
async def chat_completions(req: Request):
    oai_body = await req.json()
    body = openai_compat.to_anthropic(oai_body)
    url, headers, base = await _dispatch(req, body)
    started = time.time()
    if body.get("stream"):
        return _stream_passthrough(url, headers, body, base, started,
                                   translate_model=base["routed_model"])
    upstream = await _run_with_escalation(url, headers, body, base, started)
    if upstream.status_code != 200:
        return Response(content=upstream.content, status_code=upstream.status_code)
    return JSONResponse(openai_compat.from_anthropic(upstream.json()))


@app.get("/health")
async def health():
    return JSONResponse({"ok": True, "default_model": policy.default_model})
