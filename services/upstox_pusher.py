# services/upstox_pusher.py
"""
Fetches a fresh Upstox TOTP and:
  - (direct mode)   calls Upstox's TOTP-login API, validates the token,
                    and writes it to UPSTOX_APP_V2.app_tokens.
  - (legacy mode)   POSTs the TOTP to the old dashboard's /api/login.

Mode is chosen by UPSTOX_DIRECT_LOGIN (default true).

The token document written to MongoDB has exactly this shape:

    {
      _id: 'upstox_access_token',
      access_token: '...',
      created_at: '2026-10-15T06:28:26.714170+05:30',   # insert-only
      updated_at: '2026-10-27T04:00:04.786728+05:30',   # every write
      source: 'dashboard',
      last_validation_status: 'success',
      last_validation_status_text: 'profile_success',
      last_validated_at: '2026-10-27T20:56:31.980715+05:30',
      last_validation_error: null,
      last_profile_user_id: '...',
      last_profile_user_name: '...',
      last_profile_broker: 'UPSTOX'
    }
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone, timedelta

import requests

from core.config import settings
from core.db import (
    push_logs_collection,
    totp_entries_collection,
    upstox_app_tokens_collection,
)
from core.security import make_totp
from services.telegram import notify_push_result
from services.upstox_api import UpstoxClient, UpstoxError

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
                "Found %d entries with issuer='%s' but UPSTOX_OWNER_EMAIL is "
                "not set — the first one will be used. Set UPSTOX_OWNER_EMAIL "
                "to disambiguate.",
                count,
                settings.UPSTOX_ISSUER,
            )

    return totp_entries_collection.find_one(query)


def _current_code(entry: dict) -> tuple[str, int]:
    """Return (code, seconds_until_rotation) for a TOTP entry."""
    period = int(entry.get("period", 30))
    totp = make_totp(
        entry["secret"],
        digits=int(entry.get("digits", 6)),
        period=period,
        algorithm=entry.get("algorithm", "SHA1"),
    )
    remaining = period - (int(time.time()) % period)
    return totp.now(), remaining


def _is_totp_error(msg: str) -> bool:
    m = (msg or "").lower()
    return any(kw in m for kw in ("totp", "expired", "invalid"))


def _save_token_to_mongo(
    *,
    access_token: str,
    profile: dict | None = None,
    validation_status: str = "success",
    validation_status_text: str = "profile_success",
    validation_error: str | None = None,
) -> None:
    """
    Upsert the single `upstox_access_token` document.

    Fields set on every write:
        access_token, updated_at, source,
        last_validation_status, last_validation_status_text,
        last_validated_at, last_validation_error,
        last_profile_user_id, last_profile_user_name, last_profile_broker

    Fields set on insert only:
        created_at
    """
    profile = profile or {}
    now_iso = datetime.now(IST).isoformat()

    doc = {
        "access_token": access_token,
        "updated_at": now_iso,
        "source": settings.UPSTOX_TOKEN_SOURCE,
        "last_validation_status": validation_status,
        "last_validation_status_text": validation_status_text,
        "last_validated_at": now_iso,
        "last_validation_error": validation_error,
        "last_profile_user_id": profile.get("user_id"),
        "last_profile_user_name": profile.get("user_name"),
        "last_profile_broker": profile.get("broker"),
    }

    upstox_app_tokens_collection.update_one(
        {"_id": settings.UPSTOX_APP_TOKEN_DOC_ID},
        {"$set": doc, "$setOnInsert": {"created_at": now_iso}},
        upsert=True,
    )


def _attempt_dashboard_push(code: str) -> tuple[int, dict]:
    """Legacy: POST the code to the Upstox dashboard's /api/login."""
    url = f"{settings.UPSTOX_DASHBOARD_URL}/api/login"
    resp = requests.post(
        url,
        json={"totp": code},
        timeout=settings.UPSTOX_PUSH_TIMEOUT,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
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
    target_url: str | None = None,
    extra: dict | None = None,
) -> dict:
    """Build a log document, insert it into push_logs, and return a summary."""
    finished = datetime.now(timezone.utc)
    doc: dict = {
        "triggered_by": triggered_by,
        "issuer": settings.UPSTOX_ISSUER,
        "target_url": target_url
        or f"{settings.UPSTOX_DASHBOARD_URL}/api/login",
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

    return {
        "success": success,
        "http_status": http_status,
        "message": response_message,
        "error": error,
        "duration_ms": doc["duration_ms"],
        "account": doc.get("account", ""),
        "user_email": doc.get("user_email", ""),
        "triggered_by": triggered_by,
        "target_url": doc["target_url"],
        "mode": (extra or {}).get("mode", "unknown"),
        "started_at": started_at,
    }


# ----------------------------------------------------------------- main job

def run_daily_push(triggered_by: str = "scheduler") -> dict:
    """Fetch a fresh Upstox TOTP and push it using the configured mode."""
    result = _run_push_core(triggered_by)
    try:
        notify_push_result(result)
    except Exception as exc:
        log.warning("Telegram notification failed: %s", exc)
    return result


def _run_push_core(triggered_by: str) -> dict:
    started_at = datetime.now(timezone.utc)
    started_perf = time.perf_counter()

    mode = "direct_api" if settings.UPSTOX_DIRECT_LOGIN else "dashboard_push"
    log.info(
        "Upstox push starting (triggered_by=%s, mode=%s)",
        triggered_by,
        mode,
    )

    entry = _find_upstox_entry()
    if not entry:
        msg = f"No TOTP entry found with issuer='{settings.UPSTOX_ISSUER}'"
        log.warning(msg)
        return _record(
            triggered_by=triggered_by,
            entry=None,
            success=False,
            error=msg,
            started_at=started_at,
            started_perf=started_perf,
            extra={"mode": mode},
        )

    try:
        code, remaining = _current_code(entry)
    except Exception as exc:
        msg = f"Failed to generate TOTP: {exc}"
        log.error(msg)
        return _record(
            triggered_by=triggered_by,
            entry=entry,
            success=False,
            error=msg,
            started_at=started_at,
            started_perf=started_perf,
            extra={"mode": mode},
        )

    if remaining <= 3:
        wait = remaining + 1
        log.info(
            "Code expires in %ds — waiting %ds for next rotation",
            remaining,
            wait,
        )
        time.sleep(wait)
        try:
            code, remaining = _current_code(entry)
        except Exception as exc:
            msg = f"Failed to generate TOTP after rotation: {exc}"
            return _record(
                triggered_by=triggered_by,
                entry=entry,
                success=False,
                error=msg,
                started_at=started_at,
                started_perf=started_perf,
                extra={"mode": mode},
            )

    extra = {
        "expires_in_at_send": remaining,
        "code_length": len(code),
        "mode": mode,
    }

    if settings.UPSTOX_DIRECT_LOGIN:
        return _push_via_upstox_api(
            triggered_by=triggered_by,
            entry=entry,
            code=code,
            started_at=started_at,
            started_perf=started_perf,
            extra=extra,
        )

    return _push_via_dashboard(
        triggered_by=triggered_by,
        entry=entry,
        code=code,
        started_at=started_at,
        started_perf=started_perf,
        extra=extra,
    )


# ----------------------------------------------------------------- direct mode

def _push_via_upstox_api(
    *,
    triggered_by: str,
    entry: dict,
    code: str,
    started_at: datetime,
    started_perf: float,
    extra: dict,
) -> dict:
    """Direct mode: call Upstox's TOTP-login API, validate, and persist."""
    try:
        client = UpstoxClient(
            api_key=settings.UPSTOX_API_KEY,
            pin=settings.UPSTOX_PIN,
            totp_url=settings.UPSTOX_TOTP_URL,
            profile_url=settings.UPSTOX_PROFILE_URL,
            timeout=settings.UPSTOX_PUSH_TIMEOUT,
        )
    except UpstoxError as exc:
        log.error("Upstox client init failed: %s", exc)
        return _record(
            triggered_by=triggered_by,
            entry=entry,
            success=False,
            error=str(exc),
            started_at=started_at,
            started_perf=started_perf,
            target_url=settings.UPSTOX_TOTP_URL,
            extra=extra,
        )

    try:
        login_resp = client.login_with_totp(code)
    except UpstoxError as exc:
        error_msg = str(exc)
        if not _is_totp_error(error_msg):
            log.warning("Upstox login failed: %s", error_msg)
            return _record(
                triggered_by=triggered_by,
                entry=entry,
                success=False,
                error=error_msg,
                started_at=started_at,
                started_perf=started_perf,
                target_url=settings.UPSTOX_TOTP_URL,
                extra=extra,
            )

        log.warning(
            "TOTP rejected by Upstox (%s) — retrying once with a fresh code",
            error_msg,
        )
        time.sleep(2)
        try:
            code, remaining = _current_code(entry)
            extra["retried"] = True
            extra["expires_in_at_send"] = remaining
            login_resp = client.login_with_totp(code)
        except Exception as exc2:
            return _record(
                triggered_by=triggered_by,
                entry=entry,
                success=False,
                error=f"Retry failed: {exc2}",
                started_at=started_at,
                started_perf=started_perf,
                target_url=settings.UPSTOX_TOTP_URL,
                extra=extra,
            )
    except Exception as exc:
        return _record(
            triggered_by=triggered_by,
            entry=entry,
            success=False,
            error=f"Unexpected error during login: {exc}",
            started_at=started_at,
            started_perf=started_perf,
            target_url=settings.UPSTOX_TOTP_URL,
            extra=extra,
        )

    access_token = login_resp.get("access_token")
    if not access_token:
        return _record(
            triggered_by=triggered_by,
            entry=entry,
            success=False,
            error="Upstox response did not contain an access_token.",
            started_at=started_at,
            started_perf=started_perf,
            target_url=settings.UPSTOX_TOTP_URL,
            extra=extra,
        )

    profile: dict = {}
    validation_status = "success"
    validation_status_text = "profile_success"
    validation_error: str | None = None
    try:
        profile_resp = client.get_profile(access_token)
        profile = (profile_resp or {}).get("data") or {}
        log.info("Upstox profile validation succeeded")
    except UpstoxError as exc:
        validation_status = "failed"
        validation_status_text = "profile_failed"
        validation_error = str(exc)
        log.warning("Upstox profile validation failed: %s", validation_error)

    try:
        _save_token_to_mongo(
            access_token=access_token,
            profile=profile,
            validation_status=validation_status,
            validation_status_text=validation_status_text,
            validation_error=validation_error,
        )
    except Exception as exc:
        return _record(
            triggered_by=triggered_by,
            entry=entry,
            success=False,
            error=f"Failed to save token to MongoDB: {exc}",
            started_at=started_at,
            started_perf=started_perf,
            target_url=settings.UPSTOX_TOTP_URL,
            extra=extra,
        )

    msg = "Access token saved successfully."
    if validation_status != "success":
        msg += " (profile validation failed)"

    log.info(
        "Upstox access token saved to MongoDB (validation=%s)",
        validation_status,
    )

    return _record(
        triggered_by=triggered_by,
        entry=entry,
        success=True,
        http_status=200,
        response_message=msg,
        started_at=started_at,
        started_perf=started_perf,
        target_url=settings.UPSTOX_TOTP_URL,
        extra=extra,
    )


# ----------------------------------------------------------------- legacy mode

def _push_via_dashboard(
    *,
    triggered_by: str,
    entry: dict,
    code: str,
    started_at: datetime,
    started_perf: float,
    extra: dict,
) -> dict:
    """Legacy: POST the TOTP to the Upstox dashboard's /api/login."""
    try:
        status, body = _attempt_dashboard_push(code)
    except requests.RequestException as exc:
        msg = f"Network error contacting Upstox dashboard: {exc}"
        log.error(msg)
        return _record(
            triggered_by=triggered_by,
            entry=entry,
            success=False,
            error=msg,
            started_at=started_at,
            started_perf=started_perf,
            extra=extra,
        )

    if status == 200 and body.get("ok"):
        msg = body.get("message") or "Access token saved."
        warn = body.get("warning")
        log.info(
            "Upstox dashboard push succeeded: %s%s",
            msg,
            f" ({warn})" if warn else "",
        )
        return _record(
            triggered_by=triggered_by,
            entry=entry,
            success=True,
            http_status=status,
            response_message=msg,
            started_at=started_at,
            started_perf=started_perf,
            extra=extra,
        )

    err_text = str(
        body.get("message") or body.get("detail") or ""
    ).lower()
    is_totp_error = status == 400 and any(
        kw in err_text for kw in ("totp", "expired", "invalid")
    )
    if is_totp_error:
        log.warning(
            "TOTP rejected (%s) — retrying once with a fresh code", err_text
        )
        time.sleep(2)
        try:
            code, remaining = _current_code(entry)
            extra["retried"] = True
            extra["expires_in_at_send"] = remaining
            status, body = _attempt_dashboard_push(code)
        except Exception as exc:
            msg = f"Retry failed: {exc}"
            return _record(
                triggered_by=triggered_by,
                entry=entry,
                success=False,
                http_status=status,
                error=msg,
                started_at=started_at,
                started_perf=started_perf,
                extra=extra,
            )
        if status == 200 and body.get("ok"):
            msg = body.get("message") or "Access token saved."
            return _record(
                triggered_by=triggered_by,
                entry=entry,
                success=True,
                http_status=status,
                response_message=msg,
                started_at=started_at,
                started_perf=started_perf,
                extra=extra,
            )

    error_msg = (
        body.get("message") or body.get("detail") or f"HTTP {status}"
    )
    log.warning("Upstox dashboard push failed: %s", error_msg)
    return _record(
        triggered_by=triggered_by,
        entry=entry,
        success=False,
        http_status=status,
        error=str(error_msg),
        started_at=started_at,
        started_perf=started_perf,
        extra=extra,
    )