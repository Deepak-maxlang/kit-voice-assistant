"""Application settings, loaded from environment variables / backend/.env."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # College system
    kit_base_url: str = "https://ht.knowvally.com"
    kit_participant_key: str = ""

    # AI providers
    gemini_api_key: str = ""
    groq_api_key: str = ""
    deepgram_api_key: str = ""
    gemini_models: str = "gemini-3.1-flash-lite,gemini-2.5-flash"
    groq_model: str = "openai/gpt-oss-120b"
    llm_primary: str = "gemini"  # "gemini" or "groq": which provider family is tried first
    groq_stt_model: str = "whisper-large-v3-turbo"
    deepgram_stt_model: str = "nova-3"
    deepgram_tts_model: str = "aura-2-thalia-en"

    # App
    session_secret: str = "change-me-in-production"
    cookie_secure: bool = False
    db_path: str = "kit_assistant.db"
    cors_origins: str = "http://localhost:5173"
    redis_url: str = ""
    docs_refresh_seconds: int = 600
    rate_limit_per_minute: int = 20
    login_rate_limit_per_minute: int = 8
    max_agent_steps: int = 5
    history_turns: int = 10
    llm_timeout_seconds: float = 12.0

    @property
    def gemini_model_list(self) -> list[str]:
        return [m.strip() for m in self.gemini_models.split(",") if m.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
