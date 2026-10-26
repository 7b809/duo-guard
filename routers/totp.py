# routers/totp.py
from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from core.config import settings
from core.db import users_collection
from core.security import (
    generate_qr_base64,
    generate_totp_secret,
    get_totp_uri,
    verify_totp_code,
)
from core.templating import flash, render

router = APIRouter()


def _current_user_email(request: Request):
    return request.session.get("user_email")


@router.get("/totp/setup")
async def setup_page(request: Request):
    email = _current_user_email(request)
    if not email:
        return RedirectResponse("/login", status_code=303)

    user = users_collection.find_one({"email": email})
    if user and user.get("totp_enabled"):
        flash(request, "Authenticator is already enabled on this account.", "info")
        return RedirectResponse("/dashboard", status_code=303)

    secret = request.session.get("pending_totp_secret")
    if not secret:
        secret = generate_totp_secret()
        request.session["pending_totp_secret"] = secret

    uri = get_totp_uri(secret, email, settings.APP_NAME)
    qr_base64 = generate_qr_base64(uri)

    return render(
        request,
        "setup_totp.html",
        {
            "secret": secret,
            "qr_base64": qr_base64,
            "otpauth_uri": uri,
        },
    )


@router.post("/totp/enable")
async def enable_totp(request: Request, code: str = Form(...)):
    email = _current_user_email(request)
    if not email:
        return RedirectResponse("/login", status_code=303)

    secret = request.session.get("pending_totp_secret")
    if not secret:
        flash(request, "Setup session expired. Please start again.", "warning")
        return RedirectResponse("/totp/setup", status_code=303)

    if not verify_totp_code(secret, code.strip()):
        flash(request, "That code is not valid. Please try again.", "danger")
        return RedirectResponse("/totp/setup", status_code=303)

    users_collection.update_one(
        {"email": email},
        {"$set": {"totp_secret": secret, "totp_enabled": True}},
    )
    request.session.pop("pending_totp_secret", None)

    flash(request, "Authenticator enabled successfully.", "success")
    return RedirectResponse("/dashboard", status_code=303)


@router.post("/totp/disable")
async def disable_totp(request: Request, code: str = Form(...)):
    email = _current_user_email(request)
    if not email:
        return RedirectResponse("/login", status_code=303)

    user = users_collection.find_one({"email": email})
    if not user or not user.get("totp_enabled") or not user.get("totp_secret"):
        flash(request, "Authenticator is not enabled on this account.", "info")
        return RedirectResponse("/dashboard", status_code=303)

    if not verify_totp_code(user["totp_secret"], code.strip()):
        flash(request, "Invalid code. Authenticator was not disabled.", "danger")
        return RedirectResponse("/dashboard", status_code=303)

    users_collection.update_one(
        {"email": email},
        {"$set": {"totp_secret": None, "totp_enabled": False}},
    )

    flash(request, "Authenticator has been disabled.", "warning")
    return RedirectResponse("/dashboard", status_code=303)