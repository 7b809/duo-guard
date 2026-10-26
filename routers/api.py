# routers/api.py
import time
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse

from core.db import totp_entries_collection, users_collection
from core.masking import mask_account, sanitize_push_result
from core.security import hash_api_key, make_totp
from services.upstox_pusher import run_daily_push

router = APIRouter(prefix="/api/v1", tags=["api"])


# ---------- helpers ----------

def _extract_key(x_api_key: str | None, authorization: str | None) -> str | None:
    if x_api_key and x_api_key.strip():
        return x_api_key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def _authenticate(x_api_key: str | None, authorization: str | None) -> dict:
    """Return the user document for a valid API key, or raise 401."""
    raw = _extract_key(x_api_key, authorization)
    if not raw:
        raise HTTPException(
            status_code=401,
            detail="Missing API key. Send it in the X-API-Key header "
                   "or as 'Authorization: Bearer <key>'.",
        )

    user = users_collection.find_one({"api_key_hash": hash_api_key(raw)})
    if not user:
        raise HTTPException(status_code=401, detail="Invalid API key.")

    return user


def _build_entry(entry: dict, now: float) -> dict:
    period = int(entry.get("period", 30))
    digits = int(entry.get("digits", 6))
    try:
        totp = make_totp(
            entry["secret"],
            digits=digits,
            period=period,
            algorithm=entry.get("algorithm", "SHA1"),
        )
        code = totp.now()
    except Exception:
        code = None

    remaining = period - (int(now) % period)
    return {
        "id": str(entry["_id"]),
        "issuer": entry.get("issuer") or "",
        "account": mask_account(entry.get("account") or ""),
        "algorithm": entry.get("algorithm", "SHA1"),
        "digits": digits,
        "period": period,
        "code": code,
        "expires_in": remaining,
    }


# ---------- routes ----------

@router.get("/health")
async def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@router.get("/totp/entries")
async def list_entries(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None),
):
    """List entry metadata (no codes). Useful to discover entry IDs."""
    user = _authenticate(x_api_key, authorization)
    items = []
    for entry in totp_entries_collection.find(
        {"user_email": user["email"]}
    ).sort("created_at", -1):
        items.append(
            {
                "id": str(entry["_id"]),
                "issuer": entry.get("issuer") or "",
                "account": mask_account(entry.get("account") or ""),
                "algorithm": entry.get("algorithm", "SHA1"),
                "digits": int(entry.get("digits", 6)),
                "period": int(entry.get("period", 30)),
            }
        )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(items),
        "entries": items,
    }


@router.get("/totp/codes")
async def list_codes(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None),
):
    """Return every authenticator for the account with its current code."""
    user = _authenticate(x_api_key, authorization)
    now = time.time()

    entries = [
        _build_entry(entry, now)
        for entry in totp_entries_collection.find(
            {"user_email": user["email"]}
        ).sort("created_at", -1)
    ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(entries),
        "entries": entries,
    }


@router.get("/totp/{entry_id}/code")
async def get_code(
    entry_id: str,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None),
):
    """Return the current code for a single authenticator."""
    user = _authenticate(x_api_key, authorization)

    try:
        oid = ObjectId(entry_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid entry id.")

    entry = totp_entries_collection.find_one(
        {"_id": oid, "user_email": user["email"]}
    )
    if not entry:
        raise HTTPException(status_code=404, detail="Authenticator not found.")

    return _build_entry(entry, time.time())


@router.get("/totp/code/by-issuer/{issuer}")
async def get_code_by_issuer(
    issuer: str,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None),
):
    """Convenience: fetch the current code for the first entry matching an issuer."""
    user = _authenticate(x_api_key, authorization)
    entry = totp_entries_collection.find_one(
        {"user_email": user["email"], "issuer": issuer}
    )
    if not entry:
        raise HTTPException(status_code=404, detail="No authenticator with that issuer.")
    return _build_entry(entry, time.time())


@router.get("/me")
async def me(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None),
):
    """Sanity check that the key belongs to a real user.

    Returns the caller's own email — this is the authenticated user
    verifying their own API key, so it is *not* masked.
    """
    user = _authenticate(x_api_key, authorization)
    return {"email": user["email"]}


@router.post("/upstox/push")
def push_upstox_manual(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None),
):
    """Manually trigger the Upstox daily push using the current TOTP.

    Same effect as the dashboard's "Run now" button, but for scripts and
    other automated tools. Requires a valid API key.

    Response body::

        {
          "ok": true,
          "success": true,
          "http_status": 200,
          "message": "Access token saved successfully.",
          "error": null,
          "duration_ms": 431,
          "account": "***@***.***",
          "triggered_by": "api",
          "started_at": "2026-10-27T02:19:06.153000+00:00"
        }
    """
    _authenticate(x_api_key, authorization)

    result = run_daily_push(triggered_by="api")
    safe = sanitize_push_result(result)

    return JSONResponse(
        {"ok": result["success"], **safe},
        status_code=200 if result["success"] else 502,
    )