# services/upstox_pusher.py
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone, timedelta

import requests

from core.config import settings
from core.db import push_logs_collection, totp_entries_collection
from core.security import make_totp
from services.telegram import notify_push_result

log = logging.getLogger("duo-guard.upstox")

IST = timezone(timedelta(hours=5, minutes=30))


# ----------------------------------------------------------------- helpers

def _find_upstox_entry() -> dict | None:
    """Return the TOTP entry whose issuer matches UPSTOX_ISSUER."""
    query: dict = {"issuer": settings.UPSTOX_ISSUER}

    if settings.UPSTOX_OWNER_EMAIL:
        query["user_email"] = settings.UPSTOX_OWNER_EMAIL
    else:
        count = totp_entries_collection.count_documents(query)
        if count > 1:
            log.warning(
                "Found %d entries with issuer='%s' but UPSTOX_OWNER_EMAIL is not "
                "set — the first one will be used. Set UPSTOX_OWNER_EMAIL to "
                "disambiguate.",
                count,
                settings.UPSTOX_ISSUER,
            )

    return totp_entries_collection.find_one(query)


def _current_code(entry: dict) -> tuple[str, int]:
    """Return (6-digit code, seconds_until_rotation) for a TOTP entry."""
    period = int(entry.get("period", 30))
    totp = make_totp(
        entry["secret"],
        digits=int(entry.get("digits", 6)),
        period=period,
        algorithm=entry.get("algorithm", "SHA1"),
    )
    remaining = period - (int(time.time()) % period)
    return totp.now(), remaining


def _attempt_push(code: str) -> tuple[int, dict]:
    """POST the code to the Upstox dashboard. Returns (status, response_json)."""
    url = f"{settings.UPSTOX_DASHBOARD_URL}/api/login"
    resp = requests.post(
        url,
        json={"totp": code},
        timeout=settings.UPSTOX_PUSH_TIMEOUT,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        body = resp.json()
    except ValueError:
        body = {"raw": (resp.text or "")[:500]}
    return resp.status_code, body


def _record(
    *,
    triggered_by: str,
    entry: dict | None,
    success: bool,
    started_at: datetime,
    started_perf: float,
    http_status: int | None = None,
    response_message: str | None = None,
    error: str | None = None,
    extra: dict | None = None,
) -> dict:
    """Build a log document, insert it into push_logs, and return a summary."""
    finished = datetime.now(timezone.utc)
    doc: dict = {
        "triggered_by": triggered_by,
        "issuer": settings.UPSTOX_ISSUER,
        "target_url": f"{settings.UPSTOX_DASHBOARD_URL}/api/login",
        "success": success,
        "http_status": http_status,
        "response_message": response_message,
        "error": error,
        "started_at": started_at,
        "finished_at": finished,
        "duration_ms": int((time.perf_counter() - started_perf) * 1000),
    }
    if entry:
        doc["user_email"] = entry.get("user_email")
        doc["account"] = entry.get("account") or ""
        doc["entry_id"] = str(entry["_id"])
    if extra:
        doc.update(extra)

    try:
        push_logs_collection.insert_one(doc.copy())
    except Exception as exc:
        log.warning("Failed to save push log: %s", exc)

    # NOTE: The returned dict keeps the raw `account` / `user_email`.
    # Every display boundary (Telegram, dashboard, API responses) masks them
    # via core.masking.mask_account().
    return {
        "success": success,
        "http_status": http_status,
        "message": response_message,
        "error": error,
        "duration_ms": doc["duration_ms"],
        "account": doc.get("account", ""),
        "user_email": doc.get("user_email", ""),
        "triggered_by": triggered_by,
        "started_at": started_at,
    }


# ----------------------------------------------------------------- main job

def run_daily_push(triggered_by: str = "scheduler") -> dict:
    """Fetch a fresh Upstox TOTP and push it to the Upstox dashboard.

    Records a log document in `push_logs` and sends a Telegram notification
    in every case (success or failure).
    """
    result = _run_push_core(triggered_by)
    try:
        notify_push_result(result)
    except Exception as exc:
        log.warning("Telegram notification failed: %s", exc)
    return result


def _run_push_core(triggered_by: str) -> dict:
    started_at = datetime.now(timezone.utc)
    started_perf = time.perf_counter()

    log.info("Upstox push starting (triggered_by=%s)", triggered_by)

    entry = _find_upstox_entry()
    if not entry:
        msg = f"No TOTP entry found with issuer='{settings.UPSTOX_ISSUER}'"
        log.warning(msg)
        return _record(
            triggered_by=triggered_by, entry=None, success=False,
            error=msg, started_at=started_at, started_perf=started_perf,
        )

    try:
        code, remaining = _current_code(entry)
    except Exception as exc:
        msg = f"Failed to generate TOTP: {exc}"
        log.error(msg)
        return _record(
            triggered_by=triggered_by, entry=entry, success=False,
            error=msg, started_at=started_at, started_perf=started_perf,
        )

    if remaining <= 3:
        wait = remaining + 1
        log.info("Code expires in %ds — waiting %ds for next rotation", remaining, wait)
        time.sleep(wait)
        try:
            code, remaining = _current_code(entry)
        except Exception as exc:
            msg = f"Failed to generate TOTP after rotation: {exc}"
            return _record(
                triggered_by=triggered_by, entry=entry, success=False,
                error=msg, started_at=started_at, started_perf=started_perf,
            )

    extra = {"expires_in_at_send": remaining, "code_length": len(code)}

    try:
        status, body = _attempt_push(code)
    except requests.RequestException as exc:
        msg = f"Network error contacting Upstox dashboard: {exc}"
        log.error(msg)
        return _record(
            triggered_by=triggered_by, entry=entry, success=False,
            error=msg, started_at=started_at, started_perf=started_perf,
            extra=extra,
        )

    if status == 200 and body.get("ok"):
        msg = body.get("message") or "Access token saved."
        warn = body.get("warning")
        log.info("Upstox push succeeded: %s%s", msg, f" ({warn})" if warn else "")
        return _record(
            triggered_by=triggered_by, entry=entry, success=True,
            http_status=status, response_message=msg,
            started_at=started_at, started_perf=started_perf, extra=extra,
        )

    err_text = str(body.get("message") or body.get("detail") or "").lower()
    is_totp_error = status == 400 and any(
        kw in err_text for kw in ("totp", "expired", "invalid")
    )
    if is_totp_error:
        log.warning("TOTP rejected (%s) — retrying once with a fresh code", err_text)
        time.sleep(2)
        try:
            code, remaining = _current_code(entry)
            extra["retried"] = True
            extra["expires_in_at_send"] = remaining
            status, body = _attempt_push(code)
        except Exception as exc:
            msg = f"Retry failed: {exc}"
            return _record(
                triggered_by=triggered_by, entry=entry, success=False,
                http_status=status, error=msg,
                started_at=started_at, started_perf=started_perf, extra=extra,
            )
        if status == 200 and body.get("ok"):
            msg = body.get("message") or "Access token saved."
            return _record(
                triggered_by=triggered_by, entry=entry, success=True,
                http_status=status, response_message=msg,
                started_at=started_at, started_perf=started_perf, extra=extra,
            )

    error_msg = body.get("message") or body.get("detail") or f"HTTP {status}"
    log.warning("Upstox push failed: %s", error_msg)
    return _record(
        triggered_by=triggered_by, entry=entry, success=False,
        http_status=status, error=str(error_msg),
        started_at=started_at, started_perf=started_perf, extra=extra,
    )