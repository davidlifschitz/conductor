"""Routing policy engine. First matching rule wins."""

import fnmatch
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

TAG_PREFIX = "[conductor:"  # optional in-band tag, e.g. system starts with [conductor:plan]


@dataclass
class Decision:
    routed_model: str
    rule_name: str
    tag: str | None
    est_input_tokens: int


class Policy:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._load()

    def _load(self):
        cfg = yaml.safe_load(self.path.read_text())
        self.default_model = cfg["default_model"]
        self.rules = cfg.get("rules", [])
        self.providers = cfg.get("providers", {})
        self.escalation_cfg = cfg.get("escalation", {})
        self.context_cfg = cfg.get("context", {})

    # ---- provider resolution -------------------------------------------

    def provider_for(self, model: str) -> dict:
        for name, p in self.providers.items():
            if any(fnmatch.fnmatch(model, pat) for pat in p.get("match", [])):
                key = os.environ.get(p["api_key_env"], "")
                return {"name": name, "base_url": p["base_url"].rstrip("/"), "api_key": key}
        raise ValueError(f"No provider configured for model {model!r}")

    # ---- routing ---------------------------------------------------------

    @staticmethod
    def _extract_tag(headers: dict, system_text: str) -> str | None:
        tag = headers.get("x-conductor-tag")
        if tag:
            return tag.strip().lower()
        stripped = system_text.lstrip()
        if stripped.startswith(TAG_PREFIX):
            end = stripped.find("]")
            if end > 0:
                return stripped[len(TAG_PREFIX):end].strip().lower()
        return None

    @staticmethod
    def _system_text(body: dict) -> str:
        sys = body.get("system", "")
        if isinstance(sys, list):  # content-block form
            sys = " ".join(b.get("text", "") for b in sys if isinstance(b, dict))
        return sys or ""

    @staticmethod
    def _estimate_input_tokens(body: dict) -> int:
        # Cheap heuristic: ~4 chars/token over the serialized request.
        try:
            return len(json.dumps(body.get("messages", []))) // 4
        except (TypeError, ValueError):
            return 0

    def decide(self, body: dict, headers: dict) -> Decision:
        system_text = self._system_text(body)
        tag = self._extract_tag({k.lower(): v for k, v in headers.items()}, system_text)
        est_tokens = self._estimate_input_tokens(body)

        for rule in self.rules:
            match = rule.get("match")
            if match is None:  # unconditional rule (the default)
                return Decision(rule["route"], rule["name"], tag, est_tokens)
            if "tag" in match and tag != str(match["tag"]).lower():
                continue
            if "system_regex" in match and not re.search(match["system_regex"], system_text):
                continue
            if "min_input_tokens" in match and est_tokens < int(match["min_input_tokens"]):
                continue
            return Decision(rule["route"], rule["name"], tag, est_tokens)

        return Decision(self.default_model, "fallback-default", tag, est_tokens)
