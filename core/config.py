# core/config.py
import json
import logging
import os

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("duo-guard.config")


def _bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y", "on")
    return bool(value)


# --------------------------------------------------------------- platforms

def _legacy_upstox_platform() -> dict:
    return {
        "name": "upstox",
        "auto_refresh": _bool(
            os.getenv("UPSTOX_AUTO_REFRESH"), default=True
        ),
        "on_boot": _bool(os.getenv("UPSTOX_ON_BOOT"), default=True),
    }


def _parse_platforms() -> list[dict]:
    """
    Load the platform auto-refresh list.

    Priority:
      1. PLATFORMS env var (JSON array of {name, auto_refresh, on_boot})
      2. Legacy per-platform env vars (UPSTOX_AUTO_REFRESH / UPSTOX_ON_BOOT)

    These are DEFAULTS. Runtime overrides are stored in MongoDB and
    supersede this list — see core/runtime_config.py.
    """
    raw = (os.getenv("PLATFORMS") or "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError) as exc:
            log.warning(
                "PLATFORMS is not valid JSON (%s) — falling back to "
                "legacy per-platform env vars.",
                exc,
            )
            return [_legacy_upstox_platform()]

        if not isinstance(parsed, list):
            log.warning(
                "PLATFORMS must be a JSON array — got %s. Falling back.",
                type(parsed).__name__,
            )
            return [_legacy_upstox_platform()]

        out: list[dict] = []
        for idx, item in enumerate(parsed):
            if not isinstance(item, dict):
                log.warning(
                    "PLATFORMS[%d] is not an object — skipping.", idx
                )
                continue
            name = str(item.get("name", "")).strip().lower()
            if not name:
                log.warning(
                    "PLATFORMS[%d] is missing a 'name' field — skipping.",
                    idx,
                )
                continue
            out.append(
                {
                    "name": name,
                    "auto_refresh": _bool(
                        item.get("auto_refresh"), default=True
                    ),
                    "on_boot": _bool(item.get("on_boot"), default=True),
                }
            )

        if out:
            return out

        log.warning(
            "PLATFORMS contained no usable entries — falling back.",
        )

    return [_legacy_upstox_platform()]


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

    # ---- Upstox token freshness ----
    UPSTOX_TOKEN_FRESH_HOUR: int = int(os.getenv("UPSTOX_TOKEN_FRESH_HOUR", "3"))
    UPSTOX_TOKEN_FRESH_MINUTE: int = int(
        os.getenv("UPSTOX_TOKEN_FRESH_MINUTE", "0")
    )

    # ---- Platform auto-refresh list (parsed from .env) ----
    PLATFORMS: list[dict] = _parse_platforms()

    UPSTOX_AUTO_REFRESH: bool = _bool(
        os.getenv("UPSTOX_AUTO_REFRESH"), default=True
    )
    UPSTOX_ON_BOOT: bool = _bool(os.getenv("UPSTOX_ON_BOOT"), default=True)

    # ---- Upstox direct API mode ----
    UPSTOX_DIRECT_LOGIN: bool = _bool(
        os.getenv("UPSTOX_DIRECT_LOGIN"), default=True
    )
    UPSTOX_API_KEY: str = os.getenv("UPSTOX_API_KEY", "").strip()
    UPSTOX_PIN: str = os.getenv("UPSTOX_PIN", "").strip()
    UPSTOX_TOTP_URL: str = os.getenv(
        "UPSTOX_TOTP_URL",
        "https://api.upstox.com/v2/totp-login/authorization/token",
    )
    UPSTOX_PROFILE_URL: str = os.getenv(
        "UPSTOX_PROFILE_URL", "https://api.upstox.com/v2/user/profile"
    )
    UPSTOX_TOKEN_SOURCE: str = (
        os.getenv("UPSTOX_TOKEN_SOURCE", "dashboard").strip() or "dashboard"
    )

    # ---- Upstox legacy dashboard target (fallback only) ----
    UPSTOX_DASHBOARD_URL: str = os.getenv(
        "UPSTOX_DASHBOARD_URL", "http://127.0.0.1:8000"
    ).rstrip("/")
    UPSTOX_ISSUER: str = os.getenv("UPSTOX_ISSUER", "Upstox")
    UPSTOX_OWNER_EMAIL: str = os.getenv("UPSTOX_OWNER_EMAIL", "").strip().lower()
    UPSTOX_PUSH_TIMEOUT: int = int(os.getenv("UPSTOX_PUSH_TIMEOUT", "60"))

    # ---- Upstox app DB ----
    UPSTOX_APP_DB_NAME: str = os.getenv("UPSTOX_APP_DB_NAME", "UPSTOX_APP_V2")
    UPSTOX_APP_TOKENS_COLLECTION: str = os.getenv(
        "UPSTOX_APP_TOKENS_COLLECTION", "app_tokens"
    )
    UPSTOX_APP_TOKEN_DOC_ID: str = os.getenv(
        "UPSTOX_APP_TOKEN_DOC_ID", "upstox_access_token"
    )

    # ---- Telegram (outbound notifications) ----
    TELEGRAM_ENABLED: bool = _bool(os.getenv("TELEGRAM_ENABLED"), default=True)
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    # ---- Telegram bot (interactive) ----
    BOT_POLLING_ENABLED: bool = _bool(
        os.getenv("BOT_POLLING_ENABLED"), default=True
    )
    BOT_POLL_TIMEOUT_SECONDS: int = int(
        os.getenv("BOT_POLL_TIMEOUT_SECONDS", "25")
    )

    # ---- Masking ----
    MASK_SENSITIVE_DATA: bool = _bool(
        os.getenv("MASK_SENSITIVE_DATA"), default=True
    )

    # ---- Security monitoring ----
    SECURITY_ENABLED: bool = _bool(
        os.getenv("SECURITY_ENABLED"), default=True
    )
    LOGIN_MAX_FAILURES: int = int(os.getenv("LOGIN_MAX_FAILURES", "5"))
    LOGIN_WINDOW_SECONDS: int = int(os.getenv("LOGIN_WINDOW_SECONDS", "300"))
    LOGIN_BLOCK_SECONDS: int = int(os.getenv("LOGIN_BLOCK_SECONDS", "900"))
    LOGIN_ALERT_THRESHOLD: int = int(os.getenv("LOGIN_ALERT_THRESHOLD", "3"))
    API_MAX_FAILURES: int = int(os.getenv("API_MAX_FAILURES", "10"))
    API_WINDOW_SECONDS: int = int(os.getenv("API_WINDOW_SECONDS", "300"))
    API_BLOCK_SECONDS: int = int(os.getenv("API_BLOCK_SECONDS", "900"))
    API_ALERT_THRESHOLD: int = int(os.getenv("API_ALERT_THRESHOLD", "5"))


settings = Settings()