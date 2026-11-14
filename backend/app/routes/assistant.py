import base64

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
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


# Same text as the first chat bubble in the SPA (frontend/src/components/VoiceAssistant.jsx GREETING).
GREETINGS = {
    "public": "Hello! I can help with admissions at KIT: courses, fees, eligibility and applying. What would you like to know?",
    "student": "Hi! Ask me about your attendance, timetable, leave or on-duty requests, placements, rules or DBMS doubts.",
    "teacher": "Hello! I can show your classes and attendance, and approve or reject leave and on-duty requests.",
}
_welcome_audio: dict[str, bytes] = {}  # synthesised once per process: no LLM call, one TTS call per greeting


@router.get("/welcome-audio")
async def welcome_audio(request: Request, mode: str = "public", svc: Services = Depends(services)):
    """Spoken greeting for the assistant panel (cached MP3; 404 lets the browser stay silent)."""
    if mode not in GREETINGS:
        raise HTTPException(status_code=400, detail="Unknown mode.")
    check_rate(svc.chat_limiter, request)
    audio = _welcome_audio.get(mode)
    if audio is None:
        b64 = await svc.speech.synthesize(GREETINGS[mode])
        if not b64:
            raise HTTPException(status_code=404, detail="Speech unavailable.")
        audio = _welcome_audio[mode] = base64.b64decode(b64)
    return Response(content=audio, media_type="audio/mpeg", headers={"Cache-Control": "public, max-age=3600"})


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
