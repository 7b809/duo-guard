# scheduler/jobs.py
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from core.config import settings
from core.db import upstox_app_tokens_collection
from core.masking import mask_account
from services.telegram import notify_startup_check
from services.upstox_pusher import _find_upstox_entry, run_daily_push

log = logging.getLogger("duo-guard.scheduler")

# India Standard Time = UTC + 05:30 (no DST)
IST = timezone(timedelta(hours=5, minutes=30))

_scheduler: BackgroundScheduler | None = None


def get_scheduler() -> BackgroundScheduler | None:
    return _scheduler


# ----------------------------------------------------------------- helpers

def _today_4am_ist() -> datetime:
    """Return today's 04:00 IST as a timezone-aware datetime."""
    now_ist = datetime.now(IST)
    return now_ist.replace(
        hour=settings.SCHEDULER_HOUR,
        minute=settings.SCHEDULER_MINUTE,
        second=0, microsecond=0,
    )


def _parse_updated_at(value) -> datetime | None:
    """Parse the stored `updated_at` (string or BSON datetime) into IST."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except (ValueError, TypeError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def _read_token_doc() -> dict | None:
    try:
        return upstox_app_tokens_collection.find_one(
            {"_id": settings.UPSTOX_APP_TOKEN_DOC_ID}
        )
    except Exception as exc:
        log.warning("Failed to read Upstox token doc: %s", exc)
        return None


def _evaluate_token_status() -> dict:
    """
    Read the token document and classify its freshness.

    Returns a dict with:
        status         : "valid" | "expired" | "missing" | "unknown"
        updated_at     : datetime | None (in IST)
        scheduled_run  : today's 04:00 IST
        now_ist        : now in IST
        before_4am     : True if now is before today's scheduled run
    """
    now_ist = datetime.now(IST)
    scheduled = _today_4am_ist()
    before_4am = now_ist < scheduled

    doc = _read_token_doc()
    if not doc:
        return {
            "status": "missing",
            "updated_at": None,
            "scheduled_run": scheduled,
            "now_ist": now_ist,
            "before_4am": before_4am,
        }

    updated_at = _parse_updated_at(doc.get("updated_at"))
    if updated_at is None:
        return {
            "status": "unknown",
            "updated_at": None,
            "scheduled_run": scheduled,
            "now_ist": now_ist,
            "before_4am": before_4am,
        }

    status = "valid" if updated_at >= scheduled else "expired"
    return {
        "status": status,
        "updated_at": updated_at,
        "scheduled_run": scheduled,
        "now_ist": now_ist,
        "before_4am": before_4am,
    }


def _current_account_masked() -> str:
    """Return the masked account identifier for the configured Upstox entry."""
    try:
        entry = _find_upstox_entry()
        if entry:
            return mask_account(entry.get("account") or entry.get("user_email"))
    except Exception:
        pass
    return "—"


# ----------------------------------------------------------------- startup hook

def check_startup_token_refresh() -> None:
    """
    Called from main.lifespan after the scheduler starts.

    Flow
    ----
    1. Read the Upstox token doc from MongoDB (UPSTOX_APP_V2.app_tokens).
    2. Send a Telegram startup notification describing the token status:
         - project started
         - MongoDB token valid / expired / missing / unknown
         - if not valid and past 04:00 IST: "sending fresh TOTP to Upstox API..."
    3. If the project started AFTER today's 04:00 IST run and the token is
       not fresh, spawn a background thread to run ``run_daily_push``.
       That function sends its own final-result Telegram message.
    """
    info = _evaluate_token_status()
    account_masked = _current_account_masked()

    should_refresh = (
        not info["before_4am"]
        and info["status"] in ("expired", "missing", "unknown")
    )

    # 1. Send the startup status message
    try:
        notify_startup_check(
            status=info["status"],
            updated_at=info["updated_at"],
            before_4am=info["before_4am"],
            will_refresh=should_refresh,
            account_masked=account_masked,
        )
    except Exception as exc:
        log.warning("Startup Telegram notification failed: %s", exc)

    if info["before_4am"]:
        log.info(
            "Startup check: before %02d:%02d IST run — scheduler will handle refresh",
            settings.SCHEDULER_HOUR, settings.SCHEDULER_MINUTE,
        )
        return

    if info["status"] == "valid":
        log.info(
            "Startup check: token is valid (updated_at=%s)",
            info["updated_at"].isoformat() if info["updated_at"] else "?",
        )
        return

    log.info(
        "Startup check: triggering background refresh (status=%s, updated_at=%s)",
        info["status"],
        info["updated_at"].isoformat() if info["updated_at"] else "—",
    )

    thread = threading.Thread(
        target=run_daily_push,
        kwargs={"triggered_by": "startup_refresh"},
        daemon=True,
        name="startup-upstox-refresh",
    )
    thread.start()


# ----------------------------------------------------------------- lifecycle

def start_scheduler() -> BackgroundScheduler | None:
    global _scheduler

    if not settings.SCHEDULER_ENABLED:
        log.info("Scheduler disabled via SCHEDULER_ENABLED=false")
        return None

    if _scheduler is not None:
        return _scheduler

    _scheduler = BackgroundScheduler(timezone=settings.SCHEDULER_TIMEZONE)

    _scheduler.add_job(
        run_daily_push,
        CronTrigger(
            hour=settings.SCHEDULER_HOUR,
            minute=settings.SCHEDULER_MINUTE,
            timezone=settings.SCHEDULER_TIMEZONE,
        ),
        id="daily_upstox_push",
        kwargs={"triggered_by": "scheduler"},
        replace_existing=True,
        coalesce=True,
        misfire_grace_time=settings.SCHEDULER_MISFIRE_GRACE,
    )

    if settings.SCHEDULER_RUN_ON_START:
        _scheduler.add_job(
            run_daily_push,
            trigger="date",
            run_date=datetime.now(),
            id="upstox_push_on_start",
            kwargs={"triggered_by": "startup"},
            replace_existing=True,
        )

    _scheduler.start()

    log.info(
        "Scheduler started — daily Upstox push at %02d:%02d %s",
        settings.SCHEDULER_HOUR,
        settings.SCHEDULER_MINUTE,
        settings.SCHEDULER_TIMEZONE,
    )
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
        except Exception:
            pass
        _scheduler = None
        log.info("Scheduler stopped")