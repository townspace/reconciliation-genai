"""
ai/client.py
------------
Provider-agnostic LLM access for the reconciliation framework.

Design goal: the whole framework must RUN with zero credentials and UPGRADE
transparently to a real LLM when an API key is present.

  - OpenAIClient    : calls the real OpenAI Chat Completions API (urllib, no
                      SDK dep). Reads OPENAI_API_KEY from the environment.
  - AnthropicClient : calls the real Claude Messages API (urllib, no SDK dep).
                      Reads ANTHROPIC_API_KEY from the environment.
  - OfflineClient   : a null client whose calls return None, forcing each AI
                      feature to use its deterministic heuristic fallback.

  default_client()  : OpenAIClient if OPENAI_API_KEY is set, else
                      AnthropicClient if ANTHROPIC_API_KEY is set, else
                      OfflineClient.

Every higher-level AI feature follows the same pattern:

    resp = client.complete_json(system, prompt)
    if resp is None:            # offline, or the call failed
        resp = <heuristic>()    # deterministic local logic

So results are always produced; Claude simply makes them sharper when available.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Optional

# Endpoint + version are stable for the Claude Messages API.
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
# Endpoint is stable for the OpenAI Chat Completions API.
OPENAI_URL = "https://api.openai.com/v1/chat/completions"

# gpt-4o-mini is the sensible default for many small classification calls
# (fast + economical). Override with RECON_LLM_MODEL, e.g. "gpt-4o".
DEFAULT_OPENAI_MODEL = os.environ.get("RECON_LLM_MODEL", "gpt-4o-mini")
# Default Claude model, used only when the Anthropic client is selected.
DEFAULT_ANTHROPIC_MODEL = os.environ.get("RECON_LLM_MODEL", "claude-sonnet-4-6")


def _safe_json(text: str):
    """Best-effort parse of model output into a dict/list, tolerating fences."""
    if not text:
        return None
    t = text.strip()
    # strip ```json ... ``` or ``` ... ``` fences
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except Exception:
        pass
    # fall back to the first {...} or [...] span
    for opener, closer in (("{", "}"), ("[", "]")):
        i, j = t.find(opener), t.rfind(closer)
        if i != -1 and j != -1 and j > i:
            try:
                return json.loads(t[i:j + 1])
            except Exception:
                continue
    return None


class LLMClient:
    """Interface every client implements."""
    live: bool = False
    model: str = ""
    provider: str = ""

    def complete(self, system: str, prompt: str, max_tokens: int = 512) -> Optional[str]:
        raise NotImplementedError

    def complete_json(self, system: str, prompt: str, max_tokens: int = 512):
        raise NotImplementedError


class OpenAIClient(LLMClient):
    """Calls the real OpenAI Chat Completions API. Any failure degrades to None."""
    live = True
    provider = "openai"

    def __init__(self, api_key: Optional[str] = None,
                 model: str = DEFAULT_OPENAI_MODEL, timeout: int = 30):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.model = model
        self.timeout = timeout

    def complete(self, system: str, prompt: str, max_tokens: int = 512,
                 response_format: Optional[dict] = None) -> Optional[str]:
        if not self.api_key:
            return None
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            # OpenAI carries the system prompt as the first message.
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        if response_format is not None:
            payload["response_format"] = response_format
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(OPENAI_URL, data=body, method="POST")
        req.add_header("content-type", "application/json")
        req.add_header("authorization", f"Bearer {self.api_key}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            choices = data.get("choices", [])
            if not choices:
                return None
            text = (choices[0].get("message", {}).get("content") or "").strip()
            return text or None
        except Exception:
            # Network error, auth error, rate limit, malformed response -> fall back.
            return None

    def complete_json(self, system: str, prompt: str, max_tokens: int = 512):
        sys = system + "\nRespond with ONLY valid JSON. No prose, no markdown fences."
        # Ask the API for guaranteed-JSON output where supported; _safe_json
        # still guards against models/endpoints that ignore the hint.
        text = self.complete(sys, prompt, max_tokens,
                             response_format={"type": "json_object"})
        return _safe_json(text or "")


class AnthropicClient(LLMClient):
    """Calls the real Claude Messages API. Any failure degrades to None."""
    live = True
    provider = "anthropic"

    def __init__(self, api_key: Optional[str] = None,
                 model: str = DEFAULT_ANTHROPIC_MODEL, timeout: int = 30):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.model = model
        self.timeout = timeout

    def complete(self, system: str, prompt: str, max_tokens: int = 512) -> Optional[str]:
        if not self.api_key:
            return None
        body = json.dumps({
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }).encode("utf-8")
        req = urllib.request.Request(ANTHROPIC_URL, data=body, method="POST")
        req.add_header("content-type", "application/json")
        req.add_header("x-api-key", self.api_key)
        req.add_header("anthropic-version", ANTHROPIC_VERSION)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            parts = [b.get("text", "") for b in data.get("content", [])
                     if b.get("type") == "text"]
            text = "".join(parts).strip()
            return text or None
        except Exception:
            # Network error, auth error, rate limit, malformed response -> fall back.
            return None

    def complete_json(self, system: str, prompt: str, max_tokens: int = 512):
        sys = system + "\nRespond with ONLY valid JSON. No prose, no markdown fences."
        return _safe_json(self.complete(sys, prompt, max_tokens) or "")


class OfflineClient(LLMClient):
    """No credentials available: every call returns None so heuristics run."""
    live = False
    model = "offline-heuristic"
    provider = "offline"

    def complete(self, system: str, prompt: str, max_tokens: int = 512) -> Optional[str]:
        return None

    def complete_json(self, system: str, prompt: str, max_tokens: int = 512):
        return None


def default_client() -> LLMClient:
    """Pick a client from the environment.

    OpenAIClient when OPENAI_API_KEY is set, else AnthropicClient when
    ANTHROPIC_API_KEY is set, else OfflineClient (deterministic heuristics).
    """
    if os.environ.get("OPENAI_API_KEY"):
        return OpenAIClient()
    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicClient()
    return OfflineClient()
