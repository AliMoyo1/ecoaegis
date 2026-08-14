"""SHE Platform configuration. Pattern from ThemisIQ config.py (guide 5.1)."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    SECRET_KEY: str = os.getenv("SECRET_KEY", "change-me-in-production")
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8080"))
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    DB_PATH: str = os.getenv("DB_PATH", "sheplatform.db")
    BCRYPT_ROUNDS: int = int(os.getenv("BCRYPT_ROUNDS", "12"))
    SESSION_TTL_HOURS: int = int(os.getenv("SESSION_TTL_HOURS", "24"))

    # AI - provider selector: kimi | deepseek | gemini | anthropic | openai-compatible
    AI_PROVIDER: str = os.getenv("AI_PROVIDER", "kimi")

    # Kimi via Hetzner Inference API (free while experimental)
    KIMI_API_KEY: str = os.getenv("KIMI_API_KEY", os.getenv("HETZNER_API_KEY", ""))
    KIMI_BASE_URL: str = os.getenv("KIMI_BASE_URL", "https://inference.hetzner.com/api/v1")
    KIMI_MODEL: str = os.getenv("KIMI_MODEL", "Kimi-K2.7-Code")

    # DeepSeek direct
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

    # Gemini (Google AI Studio / Vertex OpenAI-compatible endpoint)
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_BASE_URL: str = os.getenv("GEMINI_BASE_URL",
                                    "https://generativelanguage.googleapis.com/v1beta/openai")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    # Generic OpenAI-compatible / Anthropic fallback
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    AI_BASE_URL: str = os.getenv("AI_BASE_URL", "")
    AI_API_KEY: str = os.getenv("AI_API_KEY", "")
    AI_MODEL: str = os.getenv("AI_MODEL", "Kimi-K2.7-Code")

    # Vision AI provider override (defaults to AI_PROVIDER; Gemini/Anthropic recommended)
    VISION_AI_PROVIDER: str = os.getenv("VISION_AI_PROVIDER", os.getenv("AI_PROVIDER", "kimi"))
    VISION_AI_MODEL: str = os.getenv("VISION_AI_MODEL", "")
    VISION_AI_BASE_URL: str = os.getenv("VISION_AI_BASE_URL", "")
    VISION_AI_API_KEY: str = os.getenv("VISION_AI_API_KEY", "")

    # Email
    EMAIL_PROVIDER: str = os.getenv("EMAIL_PROVIDER", "console")
    SMTP_HOST: str = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")

    # ThemisIQ integration (Phase 3, see SHE_THEMISIQ_INTEGRATION.md)
    THEMIS_API_BASE: str = os.getenv("THEMIS_API_BASE", "http://127.0.0.1:8080")
    THEMIS_API_KEY: str = os.getenv("THEMIS_API_KEY", "")
    THEMIS_WEBHOOK_SECRET: str = os.getenv("THEMIS_WEBHOOK_SECRET", "")
    THEMIS_SYNC_ENABLED: bool = os.getenv("THEMIS_SYNC_ENABLED", "false").lower() == "true"

    # Messaging channels
    WHATSAPP_VERIFY_TOKEN: str = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
    WHATSAPP_APP_SECRET: str = os.getenv("WHATSAPP_APP_SECRET", "")
    TWILIO_ACCOUNT_SID: str = os.getenv("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN: str = os.getenv("TWILIO_AUTH_TOKEN", "")
    TWILIO_FROM_NUMBER: str = os.getenv("TWILIO_FROM_NUMBER", "")
    SMS_DEFAULT_ORG_ID: int = int(os.getenv("SMS_DEFAULT_ORG_ID", "1"))

    # Map (guide C1) - full Leaflet XYZ URL template incl. key, e.g. a MapTiler/
    # Stadia/Geoapify free-tier URL. No default: tile.openstreetmap.org is not
    # licensed for production use, so an unset value means "not configured yet"
    # rather than silently falling back to a policy-violating tile source.
    MAP_TILE_URL_TEMPLATE: str = os.getenv("MAP_TILE_URL_TEMPLATE", "")
    MAP_TILE_ATTRIBUTION: str = os.getenv("MAP_TILE_ATTRIBUTION", "")

    def is_postgres(self) -> bool:
        return bool(self.DATABASE_URL)


settings = Settings()
