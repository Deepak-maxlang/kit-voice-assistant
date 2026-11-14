"""FastAPI entry point: wires services, middleware, API routes and the built React app."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .agent import Agent
from .cache import build_cache
from .config import get_settings
from .kit_client import KitClient
from .knowledge import KnowledgeBase
from .llm import FallbackLLM, GeminiProvider, OpenAICompatProvider
from .memory import Store
from .ratelimit import RateLimiter
from .routes import assistant, auth, history, public
from .services import Services
from .session import load_session, set_session_cookie
from .speech import SpeechService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("kit")
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


async def _refresh_docs_forever(svc: Services) -> None:
    while True:
        try:
            today = (await svc.kit.today())["date"]
        except Exception:
            today = None
        await svc.kb.refresh(today)
        await asyncio.sleep(svc.settings.docs_refresh_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    http = httpx.AsyncClient(timeout=20)
    kit = KitClient(s.kit_base_url, s.kit_participant_key)
    store = Store(s.db_path)
    providers = [GeminiProvider(s.gemini_api_key, m, http, s.llm_timeout_seconds) for m in s.gemini_model_list]
    groq = OpenAICompatProvider(s.groq_api_key, s.groq_model, http, timeout=s.llm_timeout_seconds)
    providers = [groq, *providers] if s.llm_primary.lower() == "groq" else [*providers, groq]
    svc = Services(settings=s, kit=kit, store=store, cache=build_cache(s.redis_url), kb=KnowledgeBase(kit),
                   agent=Agent(FallbackLLM(providers, call_timeout=s.llm_timeout_seconds), store, s.max_agent_steps, s.history_turns),
                   speech=SpeechService(s, http), chat_limiter=RateLimiter(s.rate_limit_per_minute),
                   login_limiter=RateLimiter(s.login_rate_limit_per_minute))
    app.state.services = svc
    task = asyncio.create_task(_refresh_docs_forever(svc))
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    await kit.aclose()
    await http.aclose()


app = FastAPI(title="KIT Voice Assistant", version="1.0.0", lifespan=lifespan)
_settings = get_settings()
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in _settings.cors_origins.split(",") if o.strip()],
                   allow_credentials=True, allow_methods=["GET", "POST"], allow_headers=["Content-Type"])


@app.middleware("http")
async def session_and_security(request: Request, call_next):
    created = False
    if request.url.path.startswith("/api"):
        svc: Services = request.app.state.services
        request.state.session, created = load_session(request, svc.store, svc.settings)
    try:
        response = await call_next(request)
    except Exception:  # never leak stack traces
        log.exception("Unhandled error")
        response = JSONResponse({"detail": "Something went wrong. Please try again."}, status_code=500)
    if created and not getattr(request.state, "session_replaced", False):
        set_session_cookie(response, request.state.session["sid"], request.app.state.services.settings)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "microphone=(self)"
    return response


for r in (auth.router, assistant.router, history.router, public.router):
    app.include_router(r)

if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def spa(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        candidate = (FRONTEND_DIST / full_path).resolve()
        if full_path and candidate.is_file() and FRONTEND_DIST in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
