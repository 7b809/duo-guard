# services/telegram.py
"""
Telegram helpers.

Two layers:
  - Low-level  telegram_api_post / telegram_api_get — used by the bot
  - High-level notify_* functions                    — used by
    scheduler.jobs, services.upstox_pusher, core.security_monitor

All messages render in IST, 12-hour format, using Telegram's HTML
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
    "manual_telegram":  "📱 Manual (Telegram bot)",
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


# ------------------------------------------------------------ low-level API

def telegram_api_post(method: str, payload: dict) -> dict | None:
    """Call any Telegram Bot API method via POST. Returns parsed JSON."""
    if not settings.TELEGRAM_BOT_TOKEN:
        return None
    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/{method}"
    try:
        resp = requests.post(url, json=payload, timeout=15)
        return resp.json()
    except requests.RequestException as exc:
        log.warning("Telegram POST %s failed: %s", method, exc)
        return None
    except ValueError:
        return None


def telegram_api_get(
    method: str, params: dict, timeout: int = 30
) -> dict | None:
    """Call any Telegram Bot API method via GET. Returns parsed JSON."""
    if not settings.TELEGRAM_BOT_TOKEN:
        return None
    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/{method}"
    try:
        resp = requests.get(url, params=params, timeout=timeout)
        return resp.json()
    except requests.RequestException as exc:
        log.warning("Telegram GET %s failed: %s", method, exc)
        return None
    except ValueError:
        return None


def _send_html(text: str) -> bool:
    """Send an HTML-formatted message to the configured Telegram chat."""
    if not settings.TELEGRAM_ENABLED:
        return False
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        log.warning(
            "Telegram is enabled but TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID "
            "are missing"
        )
        return False

    resp = telegram_api_post(
        "sendMessage",
        {
            "chat_id": settings.TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
    )
    if resp and resp.get("ok"):
        return True
    log.warning("Telegram send failed: %s", (str(resp) or "")[:300])
    return False


# ----------------------------------------------------------------- public API

def notify_startup_check(
    *,
    status: str,
    updated_at: datetime | None,
    before_4am: bool,
    will_refresh: bool,
    account_masked: str = "—",
    forced: bool = False,
) -> None:
    if not settings.TELEGRAM_ENABLED:
        return

    status_labels = {
        "valid":   "VALID (updated today)",
        "expired": "EXPIRED (not updated today)",
        "missing": "MISSING (no token doc)",
        "unknown": "UNKNOWN (cannot parse updated_at)",
    }
    status_label = status_labels.get(status, status.upper())

    lines: list[str] = [
        "🚀 <b>DuoGuard Project Started</b>",
        "",
        f"🕒 <b>Time:</b> {_format_ist(None)}",
        f"👤 <b>Account:</b> {html.escape(account_masked)}",
    ]

    if updated_at is None:
        lines.append("🗄️ <b>MongoDB Token:</b> not found / unparseable")
    else:
        lines.append(
            f"🗄️ <b>MongoDB Token updated_at:</b> {_format_ist(updated_at)}"
        )

    lines.append(f"📊 <b>Token status:</b> {status_label}")

    if forced:
        lines.append("")
        lines.append(
            "🔁 <b>Mode:</b> <code>on_boot=true</code> — forcing refresh "
            "regardless of state"
        )
        lines.append("📤 <b>Action:</b> Sending fresh TOTP to Upstox API...")
    elif will_refresh:
        lines.append("📤 <b>Action:</b> Sending fresh TOTP to Upstox API...")
    else:
        lines.append("📋 <b>Action:</b> None — scheduler will handle refresh")

    _send_html("\n".join(lines))


def notify_push_result(result: dict) -> None:
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
    mode = result.get("mode")

    lines: list[str] = [
        f"{status_emoji} <b>Upstox TOTP Push — {status_text}</b>",
        "",
        f"🕒 <b>Time:</b> {_format_ist(started)}",
        f"📤 <b>Trigger:</b> {html.escape(trigger)}",
        f"👤 <b>Account:</b> {html.escape(account_masked)}",
    ]

    if mode:
        lines.append(f"🛠️ <b>Mode:</b> {html.escape(str(mode))}")
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


def notify_login_failure(
    *,
    ip: str,
    email_masked: str,
    attempt_count: int,
    window_seconds: int,
    blocked: bool,
    multi_ip: bool = False,
) -> None:
    if not settings.TELEGRAM_ENABLED:
        return

    title = (
        "🚨 <b>Suspicious Login Activity (Multiple IPs)</b>"
        if multi_ip
        else "🚨 <b>Suspicious Login Activity</b>"
    )
    lines: list[str] = [
        title,
        "",
        f"🕒 <b>Time:</b> {_format_ist(None)}",
        f"🌐 <b>Source IP:</b> <code>{html.escape(ip)}</code>",
        f"👤 <b>Account:</b> {html.escape(email_masked)}",
    ]

    if multi_ip:
        lines.append(
            f"🔁 <b>Distinct IPs failing this account:</b> {attempt_count} "
            f"(last {window_seconds}s)"
        )
        lines.append(
            "⚠️ <b>Possible:</b> credential stuffing across multiple hosts."
        )
    else:
        lines.append(
            f"❌ <b>Failed attempts from this IP:</b> {attempt_count} "
            f"(last {window_seconds}s)"
        )

    if blocked:
        lines.append("🛑 <b>Action:</b> IP has been blocked.")
    else:
        lines.append("📋 <b>Action:</b> Monitoring — no block yet.")

    _send_html("\n".join(lines))


def notify_api_failure(
    *,
    ip: str,
    key_prefix: str,
    attempt_count: int,
    window_seconds: int,
) -> None:
    if not settings.TELEGRAM_ENABLED:
        return
    safe_prefix = html.escape(key_prefix or "—")
    lines: list[str] = [
        "🚨 <b>Suspicious API Key Attempts</b>",
        "",
        f"🕒 <b>Time:</b> {_format_ist(None)}",
        f"🌐 <b>Source IP:</b> <code>{html.escape(ip)}</code>",
        f"🔑 <b>Key prefix:</b> <code>{safe_prefix}</code>",
        f"❌ <b>Failed attempts from this IP:</b> {attempt_count} "
        f"(last {window_seconds}s)",
        "📋 <b>Action:</b> Monitoring — block after configured limit.",
    ]
    _send_html("\n".join(lines))


def notify_ip_blocked(
    *,
    ip: str,
    reason: str,
    blocked_seconds: int,
) -> None:
    if not settings.TELEGRAM_ENABLED:
        return
    minutes = max(1, blocked_seconds // 60)
    lines: list[str] = [
        "🛑 <b>IP Blocked</b>",
        "",
        f"🕒 <b>Time:</b> {_format_ist(None)}",
        f"🌐 <b>Blocked IP:</b> <code>{html.escape(ip)}</code>",
        f"📝 <b>Reason:</b> {html.escape(reason)}",
        f"⏳ <b>Duration:</b> {minutes} minute(s)",
    ]
    _send_html("\n".join(lines))