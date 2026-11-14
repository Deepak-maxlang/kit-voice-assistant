"""Shared service container and per-request helpers (session, tool context, a full assistant turn)."""
from __future__ import annotations

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
        svc.cache.set("pub:today", today, 60)
    return today


async def tool_context(svc: Services, session: dict) -> ToolContext:
    today = await college_today(svc)
    user = session.get("user")
    return ToolContext(kit=svc.kit, role=(user or {}).get("role", PUBLIC) if user else PUBLIC,
                       token=session.get("kit_token") if user else None, user=user,
                       today=today["date"], kb=svc.kb, cache=svc.cache)


def client_key(request: Request) -> str:
    session = getattr(request.state, "session", None)
    if session:
        return session["sid"]
    return request.client.host if request.client else "unknown"


def check_rate(limiter: RateLimiter, request: Request) -> None:
    if not limiter.allow(client_key(request)):
        raise HTTPException(status_code=429, detail="Too many requests. Please wait a moment.")


async def run_turn(svc: Services, session: dict, text: str, conversation_id: str | None, speak: bool) -> dict[str, Any]:
    owner = owner_key(session)
    ctx = await tool_context(svc, session)
    conv = svc.store.get_conversation(conversation_id, owner) if conversation_id else None
    if not conv:
        conversation_id = svc.store.create_conversation(owner, ctx.role, text)
    result = await svc.agent.handle(ctx, conversation_id, text)
    svc.store.add_message(conversation_id, "user", text)
    svc.store.add_message(conversation_id, "assistant", result["reply"],
                          {"actions": [{k: v for k, v in a.items() if k != "result"} for a in result["actions"]],
                           "provider": result.get("provider")})
    audio = await svc.speech.synthesize(result["reply"]) if speak else None
    return {"conversation_id": conversation_id, "reply": result["reply"], "audio_b64": audio,
            "actions": [{k: v for k, v in a.items() if k != "result"} for a in result["actions"]],
            "provider": result.get("provider")}
