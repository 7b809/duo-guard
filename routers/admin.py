# routers/admin.py
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from core.masking import sanitize_push_result
from core.templating import flash
from services.upstox_pusher import run_daily_push

router = APIRouter(prefix="/admin")


@router.post("/run-upstox-push")
def trigger_upstox_push(request: Request):
    """Session-authenticated manual trigger. Returns JSON.

    Intended for the dashboard's fetch-based "Run now" button so the UI can
    show a toast and reload the push-history table without a hard navigation.

    The result from ``run_daily_push`` contains a real ``datetime`` for
    ``started_at``, which ``json.dumps`` cannot serialize. ``sanitize_push_result``
    converts datetime/date values to ISO-8601 strings, masks ``account``, and
    drops ``user_email`` entirely.
    """
    if not request.session.get("user_email"):
        return JSONResponse(
            {"ok": False, "error": "Not authenticated."},
            status_code=401,
        )

    result = run_daily_push(triggered_by="manual")

    # JSON-safe + masked copy of the push result.
    safe = sanitize_push_result(result)

    return JSONResponse(
        {"ok": result["success"], **safe},
        status_code=200 if result["success"] else 502,
    )


@router.post("/run-upstox-push-form")
def trigger_upstox_push_form(request: Request):
    """Non-JS fallback: form POST -> flash + redirect back to dashboard."""
    if not request.session.get("user_email"):
        return RedirectResponse("/login", status_code=303)

    result = run_daily_push(triggered_by="manual")

    if result["success"]:
        flash(request, result.get("message") or "Upstox push succeeded.", "success")
    else:
        err = result.get("error") or "Unknown error"
        flash(request, f"Upstox push failed: {err}", "danger")

    return RedirectResponse("/dashboard", status_code=303)