# Conductor v0.2 — Agent Dispatch Contracts

Wave 0 (orchestrator, sequential): refactor proxy.py so feature modules are
pure logic with no HTTP concerns. Required because all three domains would
otherwise edit proxy.py — shared state breaks parallel dispatch.

Wave 1 (parallel, one agent per domain, strict file ownership):

## Agent A — Auto-escalation
**Owns:** conductor/escalate.py only.
**Goal:** Pure functions deciding when a response was too weak and what the
next tier is. `should_escalate(response_json, cfg) -> str | None` (reason) and
`next_tier(model, ladder) -> str | None`.
**Constraints:** No httpx, no fastapi, no I/O. Proxy performs the retry.
Heuristics: empty content, stop_reason max_tokens on tiny output, refusal
stop_reason, uncertainty-marker density. All thresholds from policy.yaml.
**Return:** Module + list of heuristics implemented.

## Agent B — Context portability (SKILL/REPORT injection)
**Owns:** conductor/context_layer.py only.
**Goal:** `inject(system, cfg) -> system` that prepends the contents of
~/.conductor/context/SKILL.md and REPORT.md as a synthetic system block, so
memory travels with the user across harnesses. mtime-cached reads; opt-out via
[conductor:nocontext] tag or missing files.
**Constraints:** Never mutate the caller's message list; handle both string
and content-block system formats.
**Return:** Module + injection format description.

## Agent C — OpenAI-compatible ingress
**Owns:** conductor/openai_compat.py only.
**Goal:** Translate /v1/chat/completions requests to Anthropic Messages form
(`to_anthropic`), translate responses back (`from_anthropic`), and translate
Anthropic SSE streams to OpenAI chunk format (`stream_translate`).
**Constraints:** Pure translation, no HTTP. Tool-call translation out of
scope for v0.2; text + system + multi-turn only.
**Return:** Module + coverage notes.

Wave 2 (orchestrator): register modules in proxy.py, extend policy.yaml
(escalation ladder + context config), tests, README, package.
