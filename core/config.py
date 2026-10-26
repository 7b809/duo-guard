# core/config.py
import os

from dotenv import load_dotenv

load_dotenv()


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "y", "on")


class Settings:
    MONGO_URL: str = os.getenv("MONGO_URL", "mongodb://localhost:27017")
    DB_NAME: str = os.getenv("DB_NAME", "duo_auth")
    SECRET_KEY: str = os.getenv("SECRET_KEY", "change-me-in-env")
    APP_NAME: str = os.getenv("APP_NAME", "DuoGuard")

    # ---- Scheduler ----
    SCHEDULER_ENABLED: bool = _bool(os.getenv("SCHEDULER_ENABLED"), default=True)
    SCHEDULER_TIMEZONE: str = os.getenv("SCHEDULER_TIMEZONE", "Asia/Kolkata")
    SCHEDULER_HOUR: int = int(os.getenv("SCHEDULER_HOUR", "4"))
    SCHEDULER_MINUTE: int = int(os.getenv("SCHEDULER_MINUTE", "0"))
    SCHEDULER_RUN_ON_START: bool = _bool(
        os.getenv("SCHEDULER_RUN_ON_START"), default=False
    )
    SCHEDULER_MISFIRE_GRACE: int = int(os.getenv("SCHEDULER_MISFIRE_GRACE", "3600"))

    # ---- Upstox target ----
    UPSTOX_DASHBOARD_URL: str = os.getenv(
        "UPSTOX_DASHBOARD_URL", "http://127.0.0.1:8000"
    ).rstrip("/")
    UPSTOX_ISSUER: str = os.getenv("UPSTOX_ISSUER", "Upstox")
    UPSTOX_OWNER_EMAIL: str = os.getenv("UPSTOX_OWNER_EMAIL", "").strip().lower()
    UPSTOX_PUSH_TIMEOUT: int = int(os.getenv("UPSTOX_PUSH_TIMEOUT", "60"))

    # ---- Upstox app DB (token freshness check) ----
    UPSTOX_APP_DB_NAME: str = os.getenv("UPSTOX_APP_DB_NAME", "UPSTOX_APP_V2")
    UPSTOX_APP_TOKENS_COLLECTION: str = os.getenv(
        "UPSTOX_APP_TOKENS_COLLECTION", "app_tokens"
    )
    UPSTOX_APP_TOKEN_DOC_ID: str = os.getenv(
        "UPSTOX_APP_TOKEN_DOC_ID", "upstox_access_token"
    )

    # ---- Telegram ----
    TELEGRAM_ENABLED: bool = _bool(os.getenv("TELEGRAM_ENABLED"), default=True)
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    # ---- Masking ----
    MASK_SENSITIVE_DATA: bool = _bool(
        os.getenv("MASK_SENSITIVE_DATA"), default=True
    )


settings = Settings()