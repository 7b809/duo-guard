# core/runtime_config.py
"""
Runtime configuration stored in MongoDB.

These values OVERRIDE the defaults in .env. Anything toggled via the
Telegram bot lands here and takes effect immediately (no restart
required).

Document shape:

    # runtime_config collection, _id="global"
    {
      "_id": "global",
      "scheduler_enabled": true,          # pause/resume flag
      "platforms": {
        "upstox": {"auto_refresh": true, "on_boot": true}
      },
      "updated_at": "2026-10-28T...",
      "updated_by": "telegram"
    }

To reset to .env defaults, delete this document from Mongo.

Caching: reads are cached for 2 seconds to keep the scheduler hot
path cheap. Writes invalidate the cache synchronously.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from core.db import runtime_config_collection

log = logging.getLogger("duo-guard.runtime_config")

_lock = threading.RLock()
_cache: dict | None = None
_cache_time: float = 0.0
_CACHE_TTL_SECONDS = 2.0

_DEFAULTS: dict = {"scheduler_enabled": True, "platforms": {}}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_from_db() -> dict | None:
    try:
        return runtime_config_collection.find_one({"_id": "global"})
    except Exception as exc:
        log.warning("Runtime config read failed: %s", exc)
        return None


def invalidate_cache() -> None:
    """Drop the cached read so the next get_runtime_config() hits Mongo."""
    global _cache, _cache_time
    with _lock:
        _cache = None
        _cache_time = 0.0


def get_runtime_config(force: bool = False) -> dict:
    """
    Return the current runtime config.

    Shape: {"scheduler_enabled": bool, "platforms": {name: {...}}}
    """
    global _cache, _cache_time
    with _lock:
        now = time.time()
        if not force and _cache is not None and (now - _cache_time) < _CACHE_TTL_SECONDS:
            return _cache

        doc = _load_from_db()
        if not doc:
            _cache = dict(_DEFAULTS)
        else:
            _cache = {
                "scheduler_enabled": bool(doc.get("scheduler_enabled", True)),
                "platforms": dict(doc.get("platforms", {}) or {}),
            }
        _cache_time = now
        return _cache


def _write(payload: dict, updated_by: str = "system") -> None:
    """Persist the given fields (merged with a timestamp) and invalidate cache."""
    try:
        runtime_config_collection.update_one(
            {"_id": "global"},
            {
                "$set": {
                    **payload,
                    "updated_at": _now_iso(),
                    "updated_by": updated_by,
                }
            },
            upsert=True,
        )
    except Exception as exc:
        log.warning("Runtime config write failed: %s", exc)
        raise
    invalidate_cache()


def set_scheduler_enabled(value: bool, updated_by: str = "system") -> None:
    _write({"scheduler_enabled": bool(value)}, updated_by=updated_by)


def set_platform_flag(
    name: str,
    key: str,
    value: bool,
    updated_by: str = "system",
) -> None:
    """Set auto_refresh or on_boot for a single platform."""
    name = str(name).strip().lower()
    if not name:
        raise ValueError("Platform name is required.")
    if key not in ("auto_refresh", "on_boot"):
        raise ValueError(f"Unknown platform flag: {key}")

    cfg = get_runtime_config(force=True)
    platforms = dict(cfg.get("platforms", {}))
    entry = dict(platforms.get(name, {}))
    entry[key] = bool(value)
    platforms[name] = entry
    _write({"platforms": platforms}, updated_by=updated_by)


def ensure_platform_registered(
    name: str,
    auto_refresh: bool,
    on_boot: bool,
    updated_by: str = "system",
) -> None:
    """
    Seed a platform's flags if it's not already in the runtime config.
    Safe to call repeatedly — existing entries are left untouched.
    """
    name = str(name).strip().lower()
    if not name:
        return
    cfg = get_runtime_config(force=True)
    platforms = dict(cfg.get("platforms", {}))
    if name in platforms:
        return
    platforms[name] = {
        "auto_refresh": bool(auto_refresh),
        "on_boot": bool(on_boot),
    }
    _write({"platforms": platforms}, updated_by=updated_by)