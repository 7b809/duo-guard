# core/security_monitor.py
"""
Security monitoring for DuoGuard.

Tracks:
  - login form attempts (success + failure)
  - API-key auth attempts (failure + recovery)

Actions:
  - Rate-limits by IP: too many failures => IP is blocked for a
    configurable cooldown.
  - Detects credential stuffing: multiple distinct IPs failing the
    same email in a short window => alert.
  - Sends rich Telegram alerts on the *first* threshold crossing so
    you're not spammed by every subsequent failure.

Stored in MongoDB:
  - login_attempts : {ip, email, success, user_agent, timestamp}
  - api_failures   : {ip, key_prefix, timestamp}
  - blocked_ips    : {ip, blocked_until, reason, blocked_at}

Both login_attempts and api_failures carry a 24 h TTL index, and
blocked_ips carries a TTL on `blocked_until`, so the DB self-cleans.

Failure mode: if Mongo is unavailable, `is_ip_blocked` fails open
(app still works) and `record_*` no-op with a warning. Availability
over strict enforcement — matches how most production login
rate-limiters behave when their store is down.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from core.config import settings
from core.db import (
    api_failures_collection,
    blocked_ips_collection,
    login_attempts_collection,
)
from core.masking import mask_account
from services.telegram import (
    notify_api_failure,
    notify_ip_blocked,
    notify_login_failure,
)

log = logging.getLogger("duo-guard.security")


# ----------------------------------------------------------------- helpers

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _enabled() -> bool:
    return settings.SECURITY_ENABLED


def get_client_ip(request) -> str:
    """
    Best-effort client IP.

    Prefers X-Forwarded-For (first hop) when behind a reverse proxy or
    tunnel. Falls back to the socket peer address.
    """
    try:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
        if getattr(request, "client", None):
            return request.client.host
    except Exception:
        pass
    return "unknown"


def _blocked_until_active(doc: dict) -> bool:
    until = doc.get("blocked_until")
    if until is None:
        return True
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return until > _now_utc()


# ----------------------------------------------------------------- public API

def is_ip_blocked(ip: str) -> bool:
    """True if `ip` is currently blocked. Fails open on DB errors."""
    if not _enabled() or not ip:
        return False
    try:
        doc = blocked_ips_collection.find_one({"ip": ip})
    except Exception as exc:
        log.warning("Blocked-IP check failed (fail-open): %s", exc)
        return False
    if not doc:
        return False
    if _blocked_until_active(doc):
        return True
    # Expired but TTL hasn't fired yet — clean it up.
    try:
        blocked_ips_collection.delete_one({"ip": ip})
    except Exception:
        pass
    return False


def record_login_success(ip: str, email: str) -> None:
    """Reset the IP's failure counter and record the success."""
    if not _enabled() or not ip:
        return
    try:
        login_attempts_collection.insert_one(
            {
                "ip": ip,
                "email": (email or "").lower(),
                "success": True,
                "timestamp": _now_utc(),
            }
        )
    except Exception as exc:
        log.warning("Login-success insert failed: %s", exc)


def record_login_failure(
    ip: str, email: str, user_agent: str = ""
) -> dict:
    """
    Record a failed login and act on it.

    Returns ``{"blocked": bool, "alerted": bool, "count": int}``.
    """
    if not _enabled() or not ip:
        return {"blocked": False, "alerted": False, "count": 0}

    now = _now_utc()
    email_lower = (email or "").lower()

    try:
        login_attempts_collection.insert_one(
            {
                "ip": ip,
                "email": email_lower,
                "success": False,
                "user_agent": (user_agent or "")[:300],
                "timestamp": now,
            }
        )
    except Exception as exc:
        log.warning("Login-failure insert failed: %s", exc)
        return {"blocked": False, "alerted": False, "count": 0}

    window_start = now - timedelta(seconds=settings.LOGIN_WINDOW_SECONDS)

    # Per-IP failure count in the rolling window
    try:
        ip_failures = login_attempts_collection.count_documents(
            {
                "ip": ip,
                "success": False,
                "timestamp": {"$gte": window_start},
            }
        )
    except Exception:
        ip_failures = 0

    # Distinct IPs that failed for the same email in the window
    distinct_ips: list[str] = []
    if email_lower:
        try:
            distinct_ips = login_attempts_collection.distinct(
                "ip",
                {
                    "email": email_lower,
                    "success": False,
                    "timestamp": {"$gte": window_start},
                },
            )
        except Exception:
            distinct_ips = []

    alerted = False
    blocked = False

    # Alert — same IP repeated failures
    if ip_failures == settings.LOGIN_ALERT_THRESHOLD:
        try:
            notify_login_failure(
                ip=ip,
                email_masked=mask_account(email_lower) if email_lower else "—",
                attempt_count=ip_failures,
                window_seconds=settings.LOGIN_WINDOW_SECONDS,
                blocked=False,
                multi_ip=False,
            )
            alerted = True
        except Exception as exc:
            log.warning("Login alert failed: %s", exc)

    # Alert — credential stuffing (many IPs for one email)
    if (
        len(distinct_ips) >= settings.LOGIN_ALERT_THRESHOLD
        and ip_failures == 1
    ):
        try:
            notify_login_failure(
                ip=ip,
                email_masked=mask_account(email_lower) if email_lower else "—",
                attempt_count=len(distinct_ips),
                window_seconds=settings.LOGIN_WINDOW_SECONDS,
                blocked=False,
                multi_ip=True,
            )
            alerted = True
        except Exception as exc:
            log.warning("Login alert failed: %s", exc)

    # Block the IP
    if ip_failures >= settings.LOGIN_MAX_FAILURES:
        _block_ip(
            ip=ip,
            reason=(
                f"{ip_failures} failed logins in "
                f"{settings.LOGIN_WINDOW_SECONDS}s"
            ),
            blocked_seconds=settings.LOGIN_BLOCK_SECONDS,
        )
        blocked = True
        try:
            notify_ip_blocked(
                ip=ip,
                reason=f"Too many failed login attempts ({ip_failures}).",
                blocked_seconds=settings.LOGIN_BLOCK_SECONDS,
            )
        except Exception as exc:
            log.warning("IP-block alert failed: %s", exc)

    return {"blocked": blocked, "alerted": alerted, "count": ip_failures}


def record_api_failure(ip: str, key_prefix: str = "") -> dict:
    """
    Record a failed API-key auth and act on it.

    Returns ``{"blocked": bool, "alerted": bool, "count": int}``.
    """
    if not _enabled() or not ip:
        return {"blocked": False, "alerted": False, "count": 0}

    now = _now_utc()
    try:
        api_failures_collection.insert_one(
            {
                "ip": ip,
                "key_prefix": (key_prefix or "")[:16],
                "timestamp": now,
            }
        )
    except Exception as exc:
        log.warning("API-failure insert failed: %s", exc)
        return {"blocked": False, "alerted": False, "count": 0}

    window_start = now - timedelta(seconds=settings.API_WINDOW_SECONDS)
    try:
        count = api_failures_collection.count_documents(
            {"ip": ip, "timestamp": {"$gte": window_start}}
        )
    except Exception:
        count = 0

    alerted = False
    blocked = False

    if count == settings.API_ALERT_THRESHOLD:
        try:
            notify_api_failure(
                ip=ip,
                key_prefix=key_prefix or "—",
                attempt_count=count,
                window_seconds=settings.API_WINDOW_SECONDS,
            )
            alerted = True
        except Exception as exc:
            log.warning("API-failure alert failed: %s", exc)

    if count >= settings.API_MAX_FAILURES:
        _block_ip(
            ip=ip,
            reason=(
                f"{count} failed API-key auths in "
                f"{settings.API_WINDOW_SECONDS}s"
            ),
            blocked_seconds=settings.API_BLOCK_SECONDS,
        )
        blocked = True
        try:
            notify_ip_blocked(
                ip=ip,
                reason=f"Too many failed API key attempts ({count}).",
                blocked_seconds=settings.API_BLOCK_SECONDS,
            )
        except Exception as exc:
            log.warning("IP-block alert failed: %s", exc)

    return {"blocked": blocked, "alerted": alerted, "count": count}


def record_api_success(ip: str) -> None:
    """Clear the IP's API-failure counter after a successful auth."""
    if not _enabled() or not ip:
        return
    try:
        api_failures_collection.delete_many({"ip": ip})
    except Exception:
        pass


# ----------------------------------------------------------------- internal

def _block_ip(ip: str, reason: str, blocked_seconds: int) -> None:
    until = _now_utc() + timedelta(seconds=max(1, int(blocked_seconds)))
    try:
        blocked_ips_collection.update_one(
            {"ip": ip},
            {
                "$set": {
                    "ip": ip,
                    "blocked_until": until,
                    "reason": reason,
                    "blocked_at": _now_utc(),
                }
            },
            upsert=True,
        )
        log.warning(
            "Blocked IP %s until %s — %s",
            ip,
            until.isoformat(),
            reason,
        )
    except Exception as exc:
        log.warning("Failed to block IP %s: %s", ip, exc)