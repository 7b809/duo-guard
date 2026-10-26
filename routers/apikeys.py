# routers/apikeys.py
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from core.db import users_collection
from core.security import generate_api_key, hash_api_key
from core.templating import flash

router = APIRouter(prefix="/api-keys")


@router.post("/regenerate")
async def regenerate(request: Request):
    email = request.session.get("user_email")
    if not email:
        return RedirectResponse("/login", status_code=303)

    new_key = generate_api_key()
    users_collection.update_one(
        {"email": email},
        {
            "$set": {
                "api_key_hash": hash_api_key(new_key),
                "api_key_prefix": new_key[:10],
                "api_key_created_at": datetime.utcnow(),
            }
        },
    )
    # Shown exactly once, on the next dashboard render.
    request.session["new_api_key"] = new_key
    flash(request, "New API key created. Copy it now — it will not be shown again.", "warning")
    return RedirectResponse("/dashboard", status_code=303)


@router.post("/revoke")
async def revoke(request: Request):
    email = request.session.get("user_email")
    if not email:
        return RedirectResponse("/login", status_code=303)

    users_collection.update_one(
        {"email": email},
        {
            "$unset": {"api_key_hash": "", "api_key_prefix": ""},
            "$set": {"api_key_created_at": None},
        },
    )
    flash(request, "API key revoked. Existing integrations will stop working.", "info")
    return RedirectResponse("/dashboard", status_code=303)