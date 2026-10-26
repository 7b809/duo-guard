# routers/pages.py
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from core.db import totp_entries_collection, users_collection
from core.templating import render

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

    entries_cursor = totp_entries_collection.find({"user_email": email}).sort("created_at", -1)
    entries = []
    for entry in entries_cursor:
        entries.append(
            {
                "id": str(entry["_id"]),
                "issuer": entry.get("issuer") or "",
                "account": entry.get("account") or entry.get("label") or "",
                "period": int(entry.get("period", 30)),
            }
        )

    return render(
        request,
        "dashboard.html",
        {
            "user": user,
            "entries": entries,
            "has_api_key": bool(user.get("api_key_hash")),
            "api_key_prefix": user.get("api_key_prefix") or "",
            "api_key_created_at": user.get("api_key_created_at"),
        },
    )