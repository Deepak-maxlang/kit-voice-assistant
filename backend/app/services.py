"""Shared service container and per-request helpers (session, tool context, a full assistant turn)."""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request

from .agent import Agent
from .cache import Cache
from .config import Settings
from .kit_client import KitClient
from .knowledge import KnowledgeBase
from .memory import Store
from .ratelimit import RateLimiter
from .speech import SpeechService
from .tools import PUBLIC, ToolContext


@dataclass
class Services:
    settings: Settings
    kit: KitClient
    store: Store
    cache: Cache
    kb: KnowledgeBase
    agent: Agent
    speech: SpeechService
    chat_limiter: RateLimiter
    login_limiter: RateLimiter


def services(request: Request) -> Services:
    return request.app.state.services


def current_session(request: Request) -> dict:
    return request.state.session


def owner_key(session: dict) -> str:
    user = session.get("user")
    return f"user:{user['id']}" if user else f"anon:{session['sid']}"


def require_user(request: Request) -> dict:
    session = current_session(request)
    if not session.get("user"):
        raise HTTPException(status_code=401, detail="Please log in.")
    return session


async def college_today(svc: Services) -> dict:
    today = svc.cache.get("pub:today")
    if today is None:
        today = await svc.kit.today()
        svc.cache.set("pub:today", today, 120)
    return today


async def tool_context(svc: Services, session: dict) -> ToolContext:
    today = await college_today(svc)
    user = session.get("user")
    return ToolContext(kit=svc.kit, role=(user or {}).get("role", PUBLIC) if user else PUBLIC,
                       token=session.get("kit_token") if user else None, user=user,
                       today=today["date"], kb=svc.kb, cache=svc.cache)


def client_ip(request: Request) -> str:
    """Caller IP; behind Render's proxy the first X-Forwarded-For hop is the real client."""
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def client_key(request: Request) -> str:
    session = getattr(request.state, "session", None)
    if session:
        return session["sid"]
    return client_ip(request)


IP_SCALE = 4.0  # several real users (e.g. one campus Wi-Fi) can share an IP


def check_rate(limiter: RateLimiter, request: Request, subject: str | None = None) -> None:
    """Limit per session AND per IP, so dropping the cookie (a fresh session per request) doesn't bypass it.

    ``subject`` (e.g. the login id) adds a per-IP+subject bucket at the normal rate, against password guessing.
    """
    ip = client_ip(request)
    ok = limiter.allow(f"ip:{ip}", IP_SCALE) and limiter.allow(client_key(request))
    if ok and subject:
        ok = limiter.allow(f"subj:{ip}:{subject.lower()}")
    if not ok:
        raise HTTPException(status_code=429, detail="Too many requests. Please wait a moment.")


def tts_url(svc: Services, session: dict, text: str) -> str:
    tts_id = uuid.uuid4().hex
    svc.cache.set(f"tts:{tts_id}", {"text": text, "sid": session["sid"]}, 120)
    return f"/api/tts/{tts_id}"


async def run_turn(svc: Services, session: dict, text: str, conversation_id: str | None, speak: bool) -> dict[str, Any]:
    owner = owner_key(session)
    ctx = await tool_context(svc, session)
    conv = svc.store.get_conversation(conversation_id, owner) if conversation_id else None
    if not conv:
        conversation_id = svc.store.create_conversation(owner, ctx.role, text)
    result = await svc.agent.handle(ctx, conversation_id, text)

    def persist() -> None:
        svc.store.add_message(conversation_id, "user", text)
        svc.store.add_message(conversation_id, "assistant", result["reply"],
                              {"actions": [{k: v for k, v in a.items() if k != "result"} for a in result["actions"]],
                               "provider": result.get("provider")})

    await asyncio.to_thread(persist)
    # Don't wait for the whole MP3: hand back a one-time URL that streams Deepgram audio as it is generated,
    # so the browser starts speaking within a few hundred ms. Falls back to speechSynthesis on error.
    audio_url = tts_url(svc, session, result["reply"]) if speak and svc.settings.deepgram_api_key else None
    return {"conversation_id": conversation_id, "reply": result["reply"], "audio_b64": None, "audio_url": audio_url,
            "actions": [{k: v for k, v in a.items() if k != "result"} for a in result["actions"]],
            "provider": result.get("provider")}
