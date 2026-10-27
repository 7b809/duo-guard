# services/telegram_bot.py
"""
Interactive Telegram bot (long-polling).

Runs inside the DuoGuard process as a daemon thread. Uses the raw
Bot API (no external library). Only the chat configured in
TELEGRAM_CHAT_ID can interact — any other chat is refused.

Commands:
    /menu, /start, /help   — show main menu
    /status                — project + scheduler status
    /refresh               — pick a platform to hard-refresh
    /config                — runtime config editor

Callbacks (button clicks):
    menu, menu:refresh, menu:config, menu:status
    ref:<platform>
    cfg:sched:toggle
    cfg:p:<platform>:auto
    cfg:p:<platform>:boot

State kept in MongoDB:
    bot_state collection, _id="telegram_bot" -> {last_update_id: int}
"""
from __future__ import annotations

import html
import logging
import threading
import time
from datetime import datetime, timezone

from core.config import settings
from core.db import bot_state_collection
from core.platforms import all_platforms, get_platform
from core.runtime_config import (
    get_runtime_config,
    set_platform_flag,
    set_scheduler_enabled,
)
from scheduler.jobs import (
    get_scheduler,
    pause_scheduler,
    resume_scheduler,
    sync_scheduler_from_runtime,
)
from services.telegram import telegram_api_get, telegram_api_post
from services.upstox_pusher import run_daily_push

log = logging.getLogger("duo-guard.bot")

_stop_event = threading.Event()
_thread: threading.Thread | None = None

# Prevent duplicate concurrent refreshes for the same platform.
_inflight: set[str] = set()
_inflight_lock = threading.Lock()


# ----------------------------------------------------------------- guards

def _is_authorized(chat_id) -> bool:
    return str(chat_id) == str(settings.TELEGRAM_CHAT_ID)


# --------------------------------------------------------------- send helpers

def _send(chat_id, text: str, reply_markup: dict | None = None) -> dict | None:
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return telegram_api_post("sendMessage", payload)


def _edit(
    chat_id,
    message_id: int,
    text: str,
    reply_markup: dict | None = None,
) -> dict | None:
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return telegram_api_post("editMessageText", payload)


def _ack(callback_id: str, text: str | None = None, alert: bool = False) -> None:
    payload = {"callback_query_id": callback_id}
    if text:
        payload["text"] = text
        payload["show_alert"] = alert
    telegram_api_post("answerCallbackQuery", payload)


# ----------------------------------------------------------------- keyboards

def _main_menu_kb() -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "🔄 Refresh Token", "callback_data": "menu:refresh"},
            ],
            [
                {"text": "⚙️ Runtime Config", "callback_data": "menu:config"},
            ],
            [
                {"text": "📊 Status", "callback_data": "menu:status"},
            ],
        ]
    }


def _refresh_kb() -> dict:
    rows = []
    for p in all_platforms():
        rows.append(
            [{"text": f"🔄 {p.name}", "callback_data": f"ref:{p.name}"}]
        )
    rows.append([{"text": "« Back", "callback_data": "menu"}])
    return {"inline_keyboard": rows}


def _config_kb() -> dict:
    cfg = get_runtime_config()
    rows: list[list[dict]] = []

    sched = bool(cfg.get("scheduler_enabled", True))
    rows.append(
        [
            {
                "text": (
                    "⏸️ Pause scheduler"
                    if sched
                    else "▶️ Resume scheduler"
                ),
                "callback_data": "cfg:sched:toggle",
            }
        ]
    )

    for p in all_platforms():
        auto_icon = "✅" if p.auto_refresh else "❌"
        boot_icon = "✅" if p.on_boot else "❌"
        rows.append(
            [
                {
                    "text": f"{p.name} auto {auto_icon}",
                    "callback_data": f"cfg:p:{p.name}:auto",
                },
                {
                    "text": f"boot {boot_icon}",
                    "callback_data": f"cfg:p:{p.name}:boot",
                },
            ]
        )

    rows.append(
        [
            {"text": "🔄 Refresh view", "callback_data": "menu:config"},
            {"text": "« Back", "callback_data": "menu"},
        ]
    )
    return {"inline_keyboard": rows}


# ---------------------------------------------------------------- text views

def _status_text() -> str:
    cfg = get_runtime_config()
    sched_enabled = bool(cfg.get("scheduler_enabled", True))

    lines: list[str] = [
        "📊 <b>DuoGuard Status</b>",
        "",
        f"🕒 <b>Now:</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"⏰ <b>Scheduler:</b> {'▶️ running' if sched_enabled else '⏸️ paused'}",
        "",
        "<b>Platforms</b>",
    ]

    for p in all_platforms():
        auto = "✅" if p.auto_refresh else "❌"
        boot = "✅" if p.on_boot else "❌"
        lines.append(
            f"• <b>{html.escape(p.name)}</b>: auto {auto}, boot {boot}"
        )

    sched = get_scheduler()
    if sched is not None:
        try:
            jobs = sched.get_jobs()
            lines.append("")
            lines.append("<b>Scheduled jobs</b>")
            if not jobs:
                lines.append("• (none)")
            for j in jobs:
                nxt = getattr(j, "next_run_time", None)
                lines.append(
                    f"• <code>{html.escape(j.id)}</code>: "
                    f"{nxt if nxt else 'paused'}"
                )
        except Exception as exc:
            log.warning("Status read failed: %s", exc)

    return "\n".join(lines)


# ---------------------------------------------------------- refresh dispatch

def _trigger_refresh_async(platform: str) -> None:
    """Run a platform refresh in a background thread."""
    with _inflight_lock:
        if platform in _inflight:
            log.info("Refresh already in flight for %s — skipping", platform)
            return
        _inflight.add(platform)

    def worker():
        try:
            if platform == "upstox":
                run_daily_push(triggered_by="manual_telegram")
            else:
                _send(
                    settings.TELEGRAM_CHAT_ID,
                    f"⚠️ No refresh handler registered for platform "
                    f"<b>{html.escape(platform)}</b>.",
                )
        except Exception as exc:
            log.exception("Refresh failed for %s: %s", platform, exc)
            _send(
                settings.TELEGRAM_CHAT_ID,
                f"❌ Refresh <b>{html.escape(platform)}</b> failed: "
                f"{html.escape(str(exc))}",
            )
        finally:
            with _inflight_lock:
                _inflight.discard(platform)

    threading.Thread(
        target=worker,
        daemon=True,
        name=f"tg-refresh-{platform}",
    ).start()


# ------------------------------------------------------------ command handlers

def _cmd_menu(chat_id) -> None:
    _send(
        chat_id,
        "🤖 <b>DuoGuard Bot</b>\n\n"
        "Choose an option below, or use /refresh /config /status.",
        _main_menu_kb(),
    )


def _cmd_status(chat_id) -> None:
    _send(chat_id, _status_text(), _main_menu_kb())


def _cmd_refresh(chat_id) -> None:
    platforms = all_platforms()
    if not platforms:
        _send(chat_id, "⚠️ No platforms configured in .env.")
        return
    _send(
        chat_id,
        "🔄 <b>Pick a platform to refresh now</b>\n\n"
        "This calls the same code path as the daily scheduler job.",
        _refresh_kb(),
    )


def _cmd_config(chat_id) -> None:
    _send(
        chat_id,
        "⚙️ <b>Runtime configuration</b>\n\n"
        "Changes apply immediately and are persisted in MongoDB.",
        _config_kb(),
    )


def _handle_message(msg: dict) -> None:
    chat_id = msg["chat"]["id"]
    if not _is_authorized(chat_id):
        _send(chat_id, "⛔ Not authorized.")
        log.warning("Unauthorized bot message from chat_id=%s", chat_id)
        return

    text = (msg.get("text") or "").strip()
    if not text.startswith("/"):
        _cmd_menu(chat_id)
        return

    cmd = text.split()[0].split("@")[0].lower()
    if cmd in ("/start", "/menu", "/help"):
        _cmd_menu(chat_id)
    elif cmd == "/status":
        _cmd_status(chat_id)
    elif cmd == "/refresh":
        _cmd_refresh(chat_id)
    elif cmd == "/config":
        _cmd_config(chat_id)
    else:
        _send(
            chat_id,
            f"Unknown command <code>{html.escape(cmd)}</code>. "
            "Try /menu.",
        )


def _handle_callback(cb: dict) -> None:
    chat_id = cb["message"]["chat"]["id"]
    message_id = cb["message"]["message_id"]
    data = cb.get("data") or ""
    cbid = cb["id"]

    if not _is_authorized(chat_id):
        _ack(cbid, "Not authorized.", alert=True)
        log.warning("Unauthorized bot callback from chat_id=%s", chat_id)
        return

    # ---- navigation ----
    if data == "menu":
        _ack(cbid)
        _edit(
            chat_id,
            message_id,
            "🤖 <b>DuoGuard Bot</b>\n\nChoose an option below:",
            _main_menu_kb(),
        )
        return

    if data == "menu:refresh":
        _ack(cbid)
        _edit(
            chat_id,
            message_id,
            "🔄 <b>Pick a platform to refresh now</b>",
            _refresh_kb(),
        )
        return

    if data == "menu:config":
        _ack(cbid)
        _edit(
            chat_id,
            message_id,
            "⚙️ <b>Runtime configuration</b>",
            _config_kb(),
        )
        return

    if data == "menu:status":
        _ack(cbid)
        _edit(
            chat_id,
            message_id,
            _status_text(),
            _main_menu_kb(),
        )
        return

    # ---- refresh a platform ----
    if data.startswith("ref:"):
        name = data.split(":", 1)[1]
        platform = get_platform(name)
        if not platform:
            _ack(cbid, f"Unknown platform: {name}", alert=True)
            return
        _ack(cbid, f"🔄 Refreshing {name}…")
        _edit(
            chat_id,
            message_id,
            f"⏳ Refreshing <b>{html.escape(name)}</b>…\n\n"
            "You'll get a result message when it finishes.",
            _refresh_kb(),
        )
        _trigger_refresh_async(name)
        return

    # ---- toggle scheduler ----
    if data == "cfg:sched:toggle":
        cfg = get_runtime_config()
        new_val = not bool(cfg.get("scheduler_enabled", True))
        try:
            set_scheduler_enabled(new_val, updated_by="telegram")
        except Exception as exc:
            _ack(cbid, f"Failed: {exc}", alert=True)
            return
        if new_val:
            resume_scheduler()
        else:
            pause_scheduler()
        sync_scheduler_from_runtime()
        _ack(cbid, "▶️ Scheduler resumed" if new_val else "⏸️ Scheduler paused")
        _edit(
            chat_id,
            message_id,
            "⚙️ <b>Runtime configuration</b>",
            _config_kb(),
        )
        return

    # ---- toggle a platform flag ----
    if data.startswith("cfg:p:"):
        parts = data.split(":")
        if len(parts) != 4:
            _ack(cbid, "Bad callback data.", alert=True)
            return
        _, _, name, flag = parts
        key = "auto_refresh" if flag == "auto" else "on_boot"
        platform = get_platform(name)
        if not platform:
            _ack(cbid, f"Unknown platform: {name}", alert=True)
            return

        current = platform.auto_refresh if key == "auto_refresh" else platform.on_boot
        new_val = not current
        try:
            set_platform_flag(name, key, new_val, updated_by="telegram")
        except Exception as exc:
            _ack(cbid, f"Failed: {exc}", alert=True)
            return

        sync_scheduler_from_runtime()
        _ack(cbid, f"{name} {key} → {new_val}")
        _edit(
            chat_id,
            message_id,
            "⚙️ <b>Runtime configuration</b>",
            _config_kb(),
        )
        return

    _ack(cbid, "Unknown action.", alert=True)


def _handle_update(update: dict) -> None:
    try:
        if "callback_query" in update:
            _handle_callback(update["callback_query"])
        elif "message" in update:
            _handle_message(update["message"])
    except Exception as exc:
        log.exception("Update handling failed: %s", exc)


# --------------------------------------------------------------- poll loop

def _load_offset() -> int:
    try:
        doc = bot_state_collection.find_one({"_id": "telegram_bot"})
        return int(doc.get("last_update_id", 0)) if doc else 0
    except Exception as exc:
        log.warning("Bot offset read failed: %s", exc)
        return 0


def _save_offset(offset: int) -> None:
    try:
        bot_state_collection.update_one(
            {"_id": "telegram_bot"},
            {"$set": {"last_update_id": offset}},
            upsert=True,
        )
    except Exception as exc:
        log.warning("Bot offset write failed: %s", exc)


def _loop() -> None:
    log.info("Telegram bot polling started")
    offset = _load_offset()
    poll_timeout = max(1, settings.BOT_POLL_TIMEOUT_SECONDS)

    while not _stop_event.is_set():
        try:
            data = telegram_api_get(
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": poll_timeout,
                    "allowed_updates": '["message","callback_query"]',
                },
                timeout=poll_timeout + 10,
            )
        except Exception as exc:
            log.warning("Bot poll exception: %s", exc)
            time.sleep(2)
            continue

        if not data or not data.get("ok"):
            # Network / Telegram error — back off briefly
            time.sleep(2)
            continue

        for update in data.get("result", []):
            try:
                offset = int(update["update_id"]) + 1
                _save_offset(offset)
                _handle_update(update)
            except Exception as exc:
                log.exception("Update processing failed: %s", exc)

    log.info("Telegram bot polling stopped")


# --------------------------------------------------------------- public API

def start_bot() -> None:
    """Start the polling thread (no-op if already running or disabled)."""
    global _thread

    if not settings.TELEGRAM_ENABLED:
        log.info("Telegram bot: TELEGRAM_ENABLED=false — skipping")
        return
    if not settings.BOT_POLLING_ENABLED:
        log.info("Telegram bot: BOT_POLLING_ENABLED=false — skipping")
        return
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        log.info(
            "Telegram bot: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing "
            "— skipping"
        )
        return
    if _thread and _thread.is_alive():
        return

    _stop_event.clear()
    _thread = threading.Thread(
        target=_loop,
        daemon=True,
        name="telegram-bot",
    )
    _thread.start()


def stop_bot() -> None:
    """Signal the polling thread to stop. Daemon-thread; best-effort join."""
    _stop_event.set()
    if _thread and _thread.is_alive():
        _thread.join(timeout=5)