# services/telegram.py
"""
Telegram notification helper.

Two entry points:
    notify_startup_check(...)  -> sent from scheduler.jobs on project startup
    notify_push_result(...)    -> sent by upstox_pusher on every push

Both render messages in IST, 12-hour format, using Telegram's HTML
parse mode. Account identifiers are always masked.
"""
from __future__ import annotations

import html
import logging
from datetime import datetime, timezone, timedelta

import requests

from core.config import settings
from core.masking import mask_account

log = logging.getLogger("duo-guard.telegram")

# India Standard Time = UTC + 05:30 (no DST)
IST = timezone(timedelta(hours=5, minutes=30))

_TRIGGER_LABELS = {
    "scheduler":        "⏰ Scheduler (Daily 4 AM IST)",
    "manual":           "🖐️ Manual (Dashboard)",
    "api":              "🔌 Manual (API)",
    "startup":          "🚀 Startup (Missed Run)",
    "startup_refresh":  "🚀 Startup (Token Refresh)",
}


# ----------------------------------------------------------------- helpers

def _format_ist(dt: datetime | None) -> str:
    """Format a datetime as e.g. '27 Oct 2026, 01:58:32 AM IST'."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(IST)
    return dt.strftime("%d %b %Y, %I:%M:%S %p IST")


def _trigger_label(triggered_by: str) -> str:
    return _TRIGGER_LABELS.get(triggered_by, triggered_by or "Unknown")


def _send_html(text: str) -> bool:
    """Send an HTML-formatted message to the configured Telegram chat."""
    if not settings.TELEGRAM_ENABLED:
        return False
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        log.warning(
            "Telegram is enabled but TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are missing"
        )
        return False

    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": settings.TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        resp = requests.post(url, json=payload, timeout=15)
    except requests.RequestException as exc:
        log.warning("Telegram network error: %s", exc)
        return False

    if resp.status_code == 200:
        try:
            if resp.json().get("ok"):
                return True
        except ValueError:
            pass

    log.warning(
        "Telegram send failed: HTTP %s — %s",
        resp.status_code,
        (resp.text or "")[:300],
    )
    return False


# ----------------------------------------------------------------- public API

def notify_startup_check(
    *,
    status: str,              # "valid" | "expired" | "missing" | "unknown"
    updated_at: datetime | None,
    before_4am: bool,
    will_refresh: bool,
    account_masked: str = "—",
) -> None:
    """
    Startup notification describing the MongoDB access-token status and
    (if applicable) that a fresh TOTP is being sent to the Upstox API.
    """
    if not settings.TELEGRAM_ENABLED:
        return

    lines: list[str] = [
        "🚀 <b>DuoGuard Project Started</b>",
        "",
        f"🕒 <b>Time:</b> {_format_ist(None)}",
        f"👤 <b>Account:</b> {html.escape(account_masked)}",
    ]

    if updated_at is None:
        lines.append("🗄️ <b>MongoDB Token:</b> not found / unparseable")
    else:
        lines.append(f"🗄️ <b>MongoDB Token updated_at:</b> {_format_ist(updated_at)}")

    if before_4am:
        if status == "missing":
            lines.append("⏰ <b>Status:</b> BEFORE 04:00 IST + token MISSING")
            lines.append(
                "⚠️ <b>Note:</b> Scheduler will fail at 04:00 IST — "
                "add a TOTP entry with issuer matching UPSTOX_ISSUER"
            )
        elif status == "valid":
            lines.append("⏰ <b>Status:</b> BEFORE 04:00 IST, token VALID")
            lines.append("📋 <b>Action:</b> Scheduler will refresh at 04:00 IST")
        else:
            lines.append("⏰ <b>Status:</b> BEFORE 04:00 IST, token stale")
            lines.append("📋 <b>Action:</b> Scheduler will refresh at 04:00 IST")
    elif status == "valid":
        lines.append("✅ <b>Status:</b> VALID (refreshed today)")
        lines.append("📋 <b>Action:</b> None — token is fresh")
    elif status == "expired":
        lines.append("⚠️ <b>Status:</b> EXPIRED (not refreshed today)")
        if will_refresh:
            lines.append("📤 <b>Action:</b> Sending fresh TOTP to Upstox API...")
        else:
            lines.append("📋 <b>Action:</b> None")
    elif status == "missing":
        lines.append("❌ <b>Status:</b> MISSING (no token doc in MongoDB)")
        if will_refresh:
            lines.append("📤 <b>Action:</b> Sending fresh TOTP to Upstox API...")
    else:  # unknown
        lines.append("⚠️ <b>Status:</b> UNKNOWN (cannot parse updated_at)")
        if will_refresh:
            lines.append("📤 <b>Action:</b> Sending fresh TOTP to Upstox API...")

    _send_html("\n".join(lines))


def notify_push_result(result: dict) -> None:
    """
    Send a formatted Telegram message describing an Upstox push outcome.

    ``result`` is the dict returned by ``services.upstox_pusher.run_daily_push``.
    Account identifiers are masked before display.
    """
    if not settings.TELEGRAM_ENABLED:
        return

    success = bool(result.get("success"))
    status_emoji = "✅" if success else "❌"
    status_text = "SUCCESS" if success else "FAILED"

    started = result.get("started_at") or datetime.now(timezone.utc)
    duration_ms = result.get("duration_ms")
    account_masked = mask_account(
        result.get("account") or result.get("user_email")
    )
    trigger = _trigger_label(result.get("triggered_by", ""))

    lines: list[str] = [
        f"{status_emoji} <b>Upstox TOTP Push — {status_text}</b>",
        "",
        f"🕒 <b>Time:</b> {_format_ist(started)}",
        f"📤 <b>Trigger:</b> {html.escape(trigger)}",
        f"👤 <b>Account:</b> {html.escape(account_masked)}",
    ]

    if duration_ms is not None:
        lines.append(f"⏱️ <b>Duration:</b> {duration_ms} ms")

    if success:
        message = result.get("message") or "Access token saved successfully."
        lines.append(f"📝 <b>Message:</b> {html.escape(str(message))}")
    else:
        error = result.get("error") or "Unknown error"
        http_status = result.get("http_status")
        if http_status:
            lines.append(f"🌐 <b>HTTP:</b> {http_status}")
        lines.append(f"⚠️ <b>Error:</b> {html.escape(str(error))}")

    _send_html("\n".join(lines))