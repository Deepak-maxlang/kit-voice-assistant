from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..routes.auth import public_user
from ..services import Services, check_rate, current_session, run_turn, services, tts_url

router = APIRouter(prefix="/api", tags=["assistant"])
MAX_AUDIO_BYTES = 8 * 1024 * 1024


class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = None
    speak: bool = False


@router.get("/me")
async def me(request: Request):
    session = current_session(request)
    return {"user": public_user(session.get("user")), "mode": (session.get("user") or {}).get("role", "public")}


@router.post("/chat")
async def chat(body: ChatBody, request: Request, svc: Services = Depends(services)):
    check_rate(svc.chat_limiter, request)
    result = await run_turn(svc, current_session(request), body.message.strip(), body.conversation_id, body.speak)
    return {"transcript": body.message.strip(), **result}


@router.post("/voice")
async def voice(request: Request, audio: UploadFile = File(...), conversation_id: str | None = Form(None),
                svc: Services = Depends(services)):
    check_rate(svc.chat_limiter, request)
    data = await audio.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty audio.")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio too long.")
    ctype = (audio.content_type or "audio/webm").split(";")[0]
    transcript, stt_provider = await svc.speech.transcribe(data, ctype, audio.filename or "audio.webm")
    if not transcript:
        msg = "Sorry, I couldn't hear that clearly. Could you say it again?" if stt_provider else \
            "Speech recognition is unavailable right now. Please type your question."
        return {"transcript": "", "reply": msg, "audio_b64": None,
                "audio_url": tts_url(svc, current_session(request), msg) if svc.settings.deepgram_api_key else None,
                "conversation_id": conversation_id, "actions": [], "stt_provider": stt_provider}
    result = await run_turn(svc, current_session(request), transcript, conversation_id, speak=True)
    return {"transcript": transcript, "stt_provider": stt_provider, **result}


@router.get("/tts/{tts_id}")
async def tts(tts_id: str, request: Request, svc: Services = Depends(services)):
    """Streams the spoken reply (one-time id, bound to the caller's session)."""
    item = svc.cache.get(f"tts:{tts_id}")
    if not item or item["sid"] != current_session(request)["sid"]:
        raise HTTPException(status_code=404, detail="Audio not found.")
    svc.cache.delete_prefix(f"tts:{tts_id}")
    stream = svc.speech.stream_speech(item["text"])
    try:
        first = await stream.__anext__()  # fail fast so the browser can fall back to speechSynthesis
    except StopAsyncIteration:
        raise HTTPException(status_code=502, detail="Speech service unavailable.")

    async def body():
        yield first
        async for chunk in stream:
            yield chunk

    return StreamingResponse(body(), media_type="audio/mpeg", headers={"Cache-Control": "no-store"})
