# routers/pages.py
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from core.db import push_logs_collection, totp_entries_collection, users_collection
from core.masking import mask_account
from core.templating import render
from scheduler.jobs import get_scheduler

router = APIRouter()


@router.get("/")
async def home(request: Request):
    return render(request, "index.html")


@router.get("/dashboard")
async def dashboard(request: Request):
    email = request.session.get("user_email")
    if not email:
        return RedirectResponse("/login", status_code=303)

    user = users_collection.find_one({"email": email})
    if not user:
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    entries_cursor = totp_entries_collection.find(
        {"user_email": email}
    ).sort("created_at", -1)
    entries = []
    for entry in entries_cursor:
        entries.append(
            {
                "id": str(entry["_id"]),
                "issuer": entry.get("issuer") or "",
                "account": mask_account(
                    entry.get("account") or entry.get("label") or ""
                ),
                "period": int(entry.get("period", 30)),
            }
        )

    # ---- recent Upstox push logs (account masked) ----
    recent_pushes: list[dict] = []
    try:
        for log_doc in push_logs_collection.find().sort("started_at", -1).limit(10):
            recent_pushes.append(
                {
                    "started_at": log_doc.get("started_at"),
                    "success": bool(log_doc.get("success")),
                    "http_status": log_doc.get("http_status"),
                    "account": mask_account(log_doc.get("account") or ""),
                    "duration_ms": log_doc.get("duration_ms"),
                    "response_message": log_doc.get("response_message"),
                    "error": log_doc.get("error"),
                    "triggered_by": log_doc.get("triggered_by"),
                }
            )
    except Exception:
        pass

    # ---- scheduler status ----
    scheduler_info = None
    sched = get_scheduler()
    if sched is not None:
        try:
            job = sched.get_job("daily_upstox_push")
            if job is not None:
                scheduler_info = {"next_run": job.next_run_time}
        except Exception:
            pass

    return render(
        request,
        "dashboard.html",
        {
            "user": user,
            "entries": entries,
            "has_api_key": bool(user.get("api_key_hash")),
            "api_key_prefix": user.get("api_key_prefix") or "",
            "api_key_created_at": user.get("api_key_created_at"),
            "push_logs": recent_pushes,
            "scheduler": scheduler_info,
        },
    )