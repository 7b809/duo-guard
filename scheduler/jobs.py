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
from core.platforms import (
    PlatformConfig,
    platforms_for_boot,
    platforms_for_scheduled,
)
from core.runtime_config import get_runtime_config
from services.telegram import notify_startup_check
from services.upstox_pusher import _find_upstox_entry, run_daily_push

log = logging.getLogger("duo-guard.scheduler")

# India Standard Time = UTC + 05:30 (no DST)
IST = timezone(timedelta(hours=5, minutes=30))

_scheduler: BackgroundScheduler | None = None


def get_scheduler() -> BackgroundScheduler | None:
    return _scheduler


# ----------------------------------------------------------------- helpers

def _today_at_ist(hour: int, minute: int) -> datetime:
    now_ist = datetime.now(IST)
    return now_ist.replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )


def _today_scheduled_ist() -> datetime:
    return _today_at_ist(settings.SCHEDULER_HOUR, settings.SCHEDULER_MINUTE)


def _today_fresh_threshold_ist() -> datetime:
    return _today_at_ist(
        settings.UPSTOX_TOKEN_FRESH_HOUR,
        settings.UPSTOX_TOKEN_FRESH_MINUTE,
    )


def _parse_updated_at(value) -> datetime | None:
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
    now_ist = datetime.now(IST)
    scheduled = _today_scheduled_ist()
    fresh_threshold = _today_fresh_threshold_ist()
    before_run = now_ist < scheduled

    doc = _read_token_doc()
    if not doc:
        return {
            "status": "missing",
            "updated_at": None,
            "scheduled_run": scheduled,
            "fresh_threshold": fresh_threshold,
            "now_ist": now_ist,
            "before_run": before_run,
        }

    updated_at = _parse_updated_at(doc.get("updated_at"))
    if updated_at is None:
        return {
            "status": "unknown",
            "updated_at": None,
            "scheduled_run": scheduled,
            "fresh_threshold": fresh_threshold,
            "now_ist": now_ist,
            "before_run": before_run,
        }

    status = "valid" if updated_at >= fresh_threshold else "expired"
    return {
        "status": status,
        "updated_at": updated_at,
        "scheduled_run": scheduled,
        "fresh_threshold": fresh_threshold,
        "now_ist": now_ist,
        "before_run": before_run,
    }


def _current_account_masked() -> str:
    try:
        entry = _find_upstox_entry()
        if entry:
            return mask_account(
                entry.get("account") or entry.get("user_email")
            )
    except Exception:
        pass
    return "—"


# ----------------------------------------------------------------- startup hook

def check_startup_token_refresh() -> None:
    boot_platforms = platforms_for_boot()
    if not boot_platforms:
        log.info("Startup: no platforms with on_boot=true — skipping")
        return

    log.info(
        "Startup: %d platform(s) with on_boot=true → %s",
        len(boot_platforms),
        [p.name for p in boot_platforms],
    )

    for platform in boot_platforms:
        try:
            _dispatch_startup_refresh(platform)
        except Exception as exc:
            log.warning(
                "Startup check failed for platform %s: %s",
                platform.name,
                exc,
            )


def _dispatch_startup_refresh(platform: PlatformConfig) -> None:
    if platform.name == "upstox":
        _startup_check_upstox()
    else:
        log.info(
            "No startup handler registered for platform: %s",
            platform.name,
        )


def _startup_check_upstox() -> None:
    info = _evaluate_token_status()
    account_masked = _current_account_masked()

    if settings.UPSTOX_DIRECT_LOGIN and (
        not settings.UPSTOX_API_KEY or not settings.UPSTOX_PIN
    ):
        log.warning(
            "UPSTOX_DIRECT_LOGIN=true but UPSTOX_API_KEY / UPSTOX_PIN are "
            "missing — startup refresh will fail. Add them to .env."
        )

    log.info(
        "Startup check: on_boot=true — forcing fresh token fetch "
        "(current state=%s, updated_at=%s, before_run=%s)",
        info["status"],
        info["updated_at"].isoformat() if info["updated_at"] else "—",
        info["before_run"],
    )

    try:
        notify_startup_check(
            status=info["status"],
            updated_at=info["updated_at"],
            before_4am=info["before_run"],
            will_refresh=True,
            account_masked=account_masked,
            forced=True,
        )
    except Exception as exc:
        log.warning("Startup Telegram notification failed: %s", exc)

    thread = threading.Thread(
        target=run_daily_push,
        kwargs={"triggered_by": "startup_refresh"},
        daemon=True,
        name="startup-upstox-refresh",
    )
    thread.start()


# ------------------------------------------------------- runtime reconciliation

def pause_scheduler() -> None:
    """Pause the scheduler (jobs are kept, just not fired)."""
    if _scheduler is None:
        return
    try:
        if _scheduler.running:
            _scheduler.pause()
            log.info("Scheduler paused")
    except Exception as exc:
        log.warning("Failed to pause scheduler: %s", exc)


def resume_scheduler() -> None:
    """Resume a paused scheduler."""
    if _scheduler is None:
        return
    try:
        if not _scheduler.running:
            _scheduler.resume()
            log.info("Scheduler resumed")
    except Exception as exc:
        log.warning("Failed to resume scheduler: %s", exc)


def _desired_job_ids() -> set[str]:
    """Job IDs implied by the current runtime config."""
    return {f"daily_{p.name}_push" for p in platforms_for_scheduled()}


def sync_scheduler_from_runtime() -> None:
    """
    Reconcile the running scheduler with the runtime config.

    - Pause / resume based on `scheduler_enabled`
    - Add / remove per-platform jobs based on `auto_refresh`
    """
    if _scheduler is None:
        return

    # 1) pause/resume
    cfg = get_runtime_config()
    if cfg.get("scheduler_enabled", True):
        resume_scheduler()
    else:
        pause_scheduler()

    # 2) job set reconciliation
    try:
        existing_ids = {j.id for j in _scheduler.get_jobs()}
    except Exception as exc:
        log.warning("Failed to list scheduler jobs: %s", exc)
        return

    desired_ids = _desired_job_ids()

    # Remove jobs that are no longer wanted (only per-platform jobs)
    for job_id in existing_ids:
        if job_id.startswith("daily_") and job_id not in desired_ids:
            try:
                _scheduler.remove_job(job_id)
                log.info("Removed scheduler job %s", job_id)
            except Exception as exc:
                log.warning("Failed to remove job %s: %s", job_id, exc)

    # Add jobs that should exist but don't
    for platform in platforms_for_scheduled():
        job_id = f"daily_{platform.name}_push"
        if job_id in existing_ids:
            continue
        if platform.name == "upstox":
            try:
                _scheduler.add_job(
                    run_daily_push,
                    CronTrigger(
                        hour=settings.SCHEDULER_HOUR,
                        minute=settings.SCHEDULER_MINUTE,
                        timezone=settings.SCHEDULER_TIMEZONE,
                    ),
                    id=job_id,
                    kwargs={"triggered_by": "scheduler"},
                    replace_existing=True,
                    coalesce=True,
                    misfire_grace_time=settings.SCHEDULER_MISFIRE_GRACE,
                )
                log.info("Added scheduler job %s", job_id)
            except Exception as exc:
                log.warning("Failed to add job %s: %s", job_id, exc)
        else:
            log.warning(
                "No scheduled handler registered for platform: %s",
                platform.name,
            )


# ----------------------------------------------------------------- lifecycle

def start_scheduler() -> BackgroundScheduler | None:
    global _scheduler

    if not settings.SCHEDULER_ENABLED:
        log.info("Scheduler disabled via SCHEDULER_ENABLED=false")
        return None

    if _scheduler is not None:
        return _scheduler

    _scheduler = BackgroundScheduler(timezone=settings.SCHEDULER_TIMEZONE)

    scheduled_platforms = platforms_for_scheduled()
    if not scheduled_platforms:
        log.info(
            "No platforms have auto_refresh=true — scheduler has no jobs"
        )

    for platform in scheduled_platforms:
        if platform.name == "upstox":
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
        else:
            log.warning(
                "No scheduled handler registered for platform: %s",
                platform.name,
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

    # Reflect any runtime pause state immediately
    cfg = get_runtime_config()
    if not cfg.get("scheduler_enabled", True):
        try:
            _scheduler.pause()
            log.info("Scheduler started in PAUSED state (runtime config)")
        except Exception as exc:
            log.warning("Failed to pause scheduler on start: %s", exc)

    log.info(
        "Scheduler started — platforms=%s, daily window=%02d:%02d %s, "
        "token fresh threshold=%02d:%02d IST, mode=%s",
        [p.name for p in scheduled_platforms],
        settings.SCHEDULER_HOUR,
        settings.SCHEDULER_MINUTE,
        settings.SCHEDULER_TIMEZONE,
        settings.UPSTOX_TOKEN_FRESH_HOUR,
        settings.UPSTOX_TOKEN_FRESH_MINUTE,
        "direct_api" if settings.UPSTOX_DIRECT_LOGIN else "dashboard_push",
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