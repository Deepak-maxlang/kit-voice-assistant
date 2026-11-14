from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from ..routes.auth import public_user
from ..services import Services, check_rate, current_session, run_turn, services

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
        return {"transcript": "", "reply": msg, "audio_b64": await svc.speech.synthesize(msg),
                "conversation_id": conversation_id, "actions": [], "stt_provider": stt_provider}
    result = await run_turn(svc, current_session(request), transcript, conversation_id, speak=True)
    return {"transcript": transcript, "stt_provider": stt_provider, **result}
