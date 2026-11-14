"""LLM providers with a common interface and an automatic fallback chain.

Internal message format (provider-neutral):
  {"role": "user", "content": str}
  {"role": "assistant", "content": str|None, "tool_calls": [{"id", "name", "args"}], "raw": provider parts}
  {"role": "tool", "tool_call_id": str, "name": str, "content": str}
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

log = logging.getLogger(__name__)


class ProviderError(Exception):
    def __init__(self, message: str, retryable: bool = True, cooldown: bool = True):
        super().__init__(message)
        self.retryable = retryable
        self.cooldown = cooldown  # False for request-specific failures (e.g. an empty answer)


@dataclass
class LLMResult:
    text: str | None
    tool_calls: list[dict] = field(default_factory=list)
    raw: Any = None
    provider: str = ""


class LLMProvider(Protocol):
    name: str
    async def generate(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResult: ...


def _classify(resp: httpx.Response) -> ProviderError:
    # 429 (quota) -> switch provider immediately: the next one answers faster than any backoff.
    # 5xx (transient overload) -> one quick retry with backoff + jitter.
    return ProviderError(f"HTTP {resp.status_code}: {resp.text[:600]}", retryable=resp.status_code >= 500)


class GeminiProvider:
    URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(self, api_key: str, model: str, client: httpx.AsyncClient, timeout: float = 20.0):
        self.api_key, self.model, self.client, self.timeout = api_key, model, client, timeout
        self.name = f"gemini:{model}"

    @staticmethod
    def _decl(tool: dict) -> dict:
        d = {"name": tool["name"], "description": tool["description"]}
        params = tool.get("parameters") or {}
        if params.get("properties"):
            p = {"type": "object", "properties": params["properties"]}
            if params.get("required"):
                p["required"] = params["required"]
            d["parameters"] = p
        return d

    def _contents(self, messages: list[dict]) -> list[dict]:
        contents: list[dict] = []
        for m in messages:
            if m["role"] == "user":
                contents.append({"role": "user", "parts": [{"text": m["content"]}]})
            elif m["role"] == "assistant":
                if m.get("raw") and m.get("raw_provider") == "gemini":
                    parts = m["raw"]
                else:
                    parts = []
                    if m.get("content"):
                        parts.append({"text": m["content"]})
                    for tc in m.get("tool_calls") or []:
                        parts.append({"functionCall": {"name": tc["name"], "args": tc["args"]},
                                      "thoughtSignature": "skip_thought_signature_validator"})
                if parts:
                    contents.append({"role": "model", "parts": parts})
            elif m["role"] == "tool":
                part = {"functionResponse": {"name": m["name"], "response": {"result": json.loads(m["content"])}}}
                if contents and contents[-1]["role"] == "user" and "functionResponse" in contents[-1]["parts"][0]:
                    contents[-1]["parts"].append(part)
                else:
                    contents.append({"role": "user", "parts": [part]})
        return contents

    async def generate(self, system, messages, tools):
        if not self.api_key:
            raise ProviderError("GEMINI_API_KEY not configured", retryable=False)
        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": self._contents(messages),
            "generationConfig": {"temperature": 0.2},
        }
        if "2.5" in self.model:
            body["generationConfig"]["thinkingConfig"] = {"thinkingBudget": 0}
        elif self.model.startswith("gemini-3"):
            body["generationConfig"]["thinkingConfig"] = {"thinkingLevel": "minimal"}  # lowest voice latency
        if tools:
            body["tools"] = [{"functionDeclarations": [self._decl(t) for t in tools]}]
        try:
            resp = await self.client.post(self.URL.format(model=self.model), json=body,
                                          headers={"x-goog-api-key": self.api_key}, timeout=self.timeout)
        except httpx.TimeoutException:
            raise ProviderError("timeout", retryable=False)
        except httpx.HTTPError as exc:
            raise ProviderError(f"network: {exc.__class__.__name__}")
        if resp.status_code != 200:
            raise _classify(resp)
        data = resp.json()
        cands = data.get("candidates") or []
        if not cands:
            raise ProviderError(f"no candidates: {str(data)[:200]}")
        parts = (cands[0].get("content") or {}).get("parts") or []
        text = " ".join(p["text"] for p in parts if p.get("text") and not p.get("thought")).strip() or None
        calls = [{"id": uuid.uuid4().hex[:12], "name": p["functionCall"]["name"],
                  "args": p["functionCall"].get("args") or {}} for p in parts if "functionCall" in p]
        if not text and not calls:
            reason = cands[0].get("finishReason")
            raise ProviderError(f"empty response (finishReason={reason})", retryable=False, cooldown=False)
        return LLMResult(text, calls, raw=parts, provider=self.name)


class OpenAICompatProvider:
    """Groq (OpenAI-compatible chat completions with tool calling)."""

    def __init__(self, api_key: str, model: str, client: httpx.AsyncClient,
                 base_url: str = "https://api.groq.com/openai/v1", timeout: float = 20.0, label: str = "groq"):
        self.api_key, self.model, self.client, self.base_url, self.timeout = api_key, model, client, base_url, timeout
        self.name = f"{label}:{model}"

    @staticmethod
    def _messages(system: str, messages: list[dict]) -> list[dict]:
        out = [{"role": "system", "content": system}]
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                msg: dict[str, Any] = {"role": "assistant", "content": m.get("content") or ""}
                if m.get("tool_calls"):
                    msg["tool_calls"] = [{"id": tc["id"], "type": "function",
                                          "function": {"name": tc["name"], "arguments": json.dumps(tc["args"])}}
                                         for tc in m["tool_calls"]]
                out.append(msg)
            elif m["role"] == "tool":
                out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
        return out

    async def generate(self, system, messages, tools):
        if not self.api_key:
            raise ProviderError("GROQ_API_KEY not configured", retryable=False)
        body: dict[str, Any] = {"model": self.model, "messages": self._messages(system, messages), "temperature": 0.2}
        if tools:
            body["tools"] = [{"type": "function", "function": t} for t in tools]
            body["tool_choice"] = "auto"
        try:
            resp = await self.client.post(f"{self.base_url}/chat/completions", json=body,
                                          headers={"Authorization": f"Bearer {self.api_key}"}, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise ProviderError(f"network: {exc.__class__.__name__}")
        if resp.status_code != 200:
            raise _classify(resp)
        msg = resp.json()["choices"][0]["message"]
        calls = []
        for tc in msg.get("tool_calls") or []:
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append({"id": tc.get("id") or uuid.uuid4().hex[:12], "name": tc["function"]["name"], "args": args})
        text = (msg.get("content") or "").strip() or None
        if not text and not calls:
            raise ProviderError("empty response")
        return LLMResult(text, calls, raw=None, provider=self.name)


class FallbackLLM:
    """Tries each provider in order, with exponential backoff + jitter on retryable errors."""

    def __init__(self, providers: list[LLMProvider], attempts: int = 2, base_delay: float = 0.4,
                 call_timeout: float | None = 8.0):
        self.providers = providers
        self.call_timeout = call_timeout
        self.attempts = attempts
        self.base_delay = base_delay
        self.status: dict[str, dict] = {p.name: {"ok": None, "last_error": None, "at": None} for p in providers}
        self._cooldown_until: dict[str, float] = {}
        self.cooldown_seconds = 30.0

    async def generate(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResult:
        errors = []
        now = time.time()
        # Circuit breaker: skip a provider that just failed (unless every provider is cooling down).
        ready = [p for p in self.providers if self._cooldown_until.get(p.name, 0) <= now] or self.providers
        for p in ready:
            for attempt in range(self.attempts):
                try:
                    try:
                        result = await asyncio.wait_for(p.generate(system, messages, tools), self.call_timeout)
                    except asyncio.TimeoutError:
                        # A slow provider is treated as down: don't retry it, fall over to the next one.
                        raise ProviderError(f"timeout after {self.call_timeout}s", retryable=False)
                    self._cooldown_until.pop(p.name, None)
                    self.status[p.name] = {"ok": True, "last_error": None, "at": time.time()}
                    return result
                except ProviderError as exc:
                    log.warning("LLM %s failed (attempt %d): %s", p.name, attempt + 1, exc)
                    self.status[p.name] = {"ok": False, "last_error": str(exc)[:200], "at": time.time()}
                    errors.append(f"{p.name}: {exc}")
                    if not exc.retryable or attempt == self.attempts - 1:
                        if exc.cooldown:
                            # A daily free-tier quota won't recover in seconds: park that model for 10 minutes.
                            wait = 600.0 if "PerDay" in str(exc) else self.cooldown_seconds
                            self._cooldown_until[p.name] = time.time() + wait
                        break
                    await asyncio.sleep(min(4.0, self.base_delay * 2 ** attempt) + random.uniform(0, 0.3))
        raise ProviderError("All LLM providers failed: " + " | ".join(errors), retryable=False)
