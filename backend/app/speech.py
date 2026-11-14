"""Speech services: STT (Deepgram nova-3 -> Groq Whisper) and TTS (Deepgram Aura -> browser fallback)."""
from __future__ import annotations

import base64
import logging
import time

import httpx

log = logging.getLogger(__name__)


class SpeechService:
    def __init__(self, settings, client: httpx.AsyncClient):
        self.s = settings
        self.client = client
        self.status: dict[str, dict] = {k: {"ok": None, "last_error": None, "at": None}
                                        for k in ("deepgram_stt", "groq_whisper", "deepgram_tts")}

    def _mark(self, name: str, ok: bool, err: str | None = None) -> None:
        self.status[name] = {"ok": ok, "last_error": err, "at": time.time()}

    async def _deepgram_stt(self, audio: bytes, content_type: str) -> str:
        resp = await self.client.post(
            "https://api.deepgram.com/v1/listen",
            params={"model": self.s.deepgram_stt_model, "smart_format": "true", "language": "en"},
            headers={"Authorization": f"Token {self.s.deepgram_api_key}", "Content-Type": content_type or "audio/webm"},
            content=audio, timeout=20)
        resp.raise_for_status()
        alts = resp.json()["results"]["channels"][0]["alternatives"]
        return (alts[0].get("transcript") or "").strip() if alts else ""

    async def _groq_stt(self, audio: bytes, content_type: str, filename: str) -> str:
        resp = await self.client.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {self.s.groq_api_key}"},
            data={"model": self.s.groq_stt_model, "language": "en", "response_format": "json"},
            files={"file": (filename or "audio.webm", audio, content_type or "audio/webm")}, timeout=30)
        resp.raise_for_status()
        return (resp.json().get("text") or "").strip()

    async def transcribe(self, audio: bytes, content_type: str = "audio/webm", filename: str = "audio.webm") -> tuple[str, str | None]:
        """Returns (transcript, provider). Falls back to Groq Whisper if Deepgram fails."""
        if self.s.deepgram_api_key:
            try:
                text = await self._deepgram_stt(audio, content_type)
                self._mark("deepgram_stt", True)
                return text, "deepgram"
            except Exception as exc:
                log.warning("Deepgram STT failed: %s", exc)
                self._mark("deepgram_stt", False, str(exc)[:200])
        if self.s.groq_api_key:
            try:
                text = await self._groq_stt(audio, content_type, filename)
                self._mark("groq_whisper", True)
                return text, "groq-whisper"
            except Exception as exc:
                log.warning("Groq Whisper failed: %s", exc)
                self._mark("groq_whisper", False, str(exc)[:200])
        return "", None

    async def stream_speech(self, text: str):
        """Yield MP3 bytes from Deepgram Aura as they are generated (playback starts on the first chunk)."""
        try:
            async with self.client.stream(
                    "POST", "https://api.deepgram.com/v1/speak",
                    params={"model": self.s.deepgram_tts_model, "encoding": "mp3"},
                    headers={"Authorization": f"Token {self.s.deepgram_api_key}"},
                    json={"text": text[:1900]}, timeout=20) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes():
                    yield chunk
            self._mark("deepgram_tts", True)
        except Exception as exc:
            log.warning("Deepgram TTS stream failed: %s", exc)
            self._mark("deepgram_tts", False, str(exc)[:200])

    async def synthesize(self, text: str) -> str | None:
        """Base64 MP3 from Deepgram Aura, or None so the browser falls back to speechSynthesis."""
        if not self.s.deepgram_api_key or not text:
            return None
        try:
            resp = await self.client.post(
                "https://api.deepgram.com/v1/speak", params={"model": self.s.deepgram_tts_model, "encoding": "mp3"},
                headers={"Authorization": f"Token {self.s.deepgram_api_key}"}, json={"text": text[:1900]}, timeout=20)
            resp.raise_for_status()
            self._mark("deepgram_tts", True)
            return base64.b64encode(resp.content).decode()
        except Exception as exc:
            log.warning("Deepgram TTS failed: %s", exc)
            self._mark("deepgram_tts", False, str(exc)[:200])
            return None
