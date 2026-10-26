# core/masking.py
"""
Central masking + sanitization helpers.

Any account identifier (email, phone, broker account) that appears in
a user-visible surface — dashboard, push logs, API response, or Telegram
message — is masked here. Set MASK_SENSITIVE_DATA=false in .env to see
raw values while debugging.

sanitize_push_result() also converts non-JSON-serializable values
(e.g. `datetime`) into strings so the result of run_daily_push() can be
returned directly from a FastAPI JSONResponse.
"""
import os
from datetime import date, datetime

from dotenv import load_dotenv

load_dotenv()


def _masking_enabled() -> bool:
    return os.getenv("MASK_SENSITIVE_DATA", "true").strip().lower() in (
        "1", "true", "yes", "y", "on"
    )


def mask_account(value: str | None) -> str:
    """
    Mask an account identifier for display.

    Default (MASK_SENSITIVE_DATA=true):
        user@example.com   ->  ***@***.***
        +919876543210      ->  ***
        upstox_account     ->  ***
        None / ''          ->  —

    When MASK_SENSITIVE_DATA=false, returns the value unchanged.
    """
    if not value:
        return "—"
    if not _masking_enabled():
        return value
    return "***@***.***" if "@" in value else "***"


# Alias — same behaviour, clearer at call sites that deal with emails.
mask_email = mask_account


def _json_safe_scalar(value):
    """Convert datetime/date to ISO strings so json.dumps() works."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def sanitize_push_result(result: dict) -> dict:
    """
    Return a JSON-safe, masked copy of a ``run_daily_push()`` result.

    Rules:
      - ``user_email``           -> dropped entirely (never exposed)
      - ``account``              -> masked via mask_account()
      - ``datetime``/``date``    -> converted to ISO-8601 strings
      - everything else          -> passed through
    """
    safe: dict = {}
    for key, value in (result or {}).items():
        if key == "user_email":
            continue
        if key == "account":
            safe[key] = mask_account(value)
        else:
            safe[key] = _json_safe_scalar(value)
    return safe