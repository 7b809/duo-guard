# routers/totp.py
import time
from datetime import datetime

from bson import ObjectId
from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from core.db import totp_entries_collection
from core.security import (
    is_valid_secret,
    make_totp,
    parse_otpauth_uri,
)
from core.templating import flash, render

router = APIRouter()


def _current_user_email(request: Request):
    return request.session.get("user_email")


@router.get("/totp/add")
async def add_page(request: Request):
    if not _current_user_email(request):
        return RedirectResponse("/login", status_code=303)
    return render(request, "add_totp.html")


@router.post("/totp/add")
async def add_entry(
    request: Request,
    otpauth_uri: str = Form(None),
    issuer: str = Form(None),
    account: str = Form(None),
    secret: str = Form(None),
):
    email = _current_user_email(request)
    if not email:
        return RedirectResponse("/login", status_code=303)

    parsed = None
    if otpauth_uri:
        parsed = parse_otpauth_uri(otpauth_uri)
        if not parsed:
            flash(request, "That QR code is not a valid TOTP code.", "danger")
            return RedirectResponse("/totp/add", status_code=303)
    else:
        cleaned = (secret or "").strip().replace(" ", "").upper()
        if not cleaned or not is_valid_secret(cleaned):
            flash(request, "Please enter a valid base32 secret key.", "danger")
            return RedirectResponse("/totp/add", status_code=303)
        parsed = {
            "secret": cleaned,
            "issuer": (issuer or "").strip(),
            "account": (account or "").strip(),
            "digits": 6,
            "period": 30,
            "algorithm": "SHA1",
        }

    existing = totp_entries_collection.find_one(
        {"user_email": email, "secret": parsed["secret"]}
    )
    if existing:
        flash(request, "This authenticator is already saved.", "warning")
        return RedirectResponse("/dashboard", status_code=303)

    totp_entries_collection.insert_one(
        {
            "user_email": email,
            "issuer": parsed["issuer"],
            "account": parsed["account"],
            "label": parsed["account"] or parsed["issuer"] or "Authenticator",
            "secret": parsed["secret"],
            "digits": parsed["digits"],
            "period": parsed["period"],
            "algorithm": parsed["algorithm"],
            "created_at": datetime.utcnow(),
        }
    )

    flash(request, "Authenticator added successfully.", "success")
    return RedirectResponse("/dashboard", status_code=303)


@router.post("/totp/{entry_id}/delete")
async def delete_entry(request: Request, entry_id: str):
    email = _current_user_email(request)
    if not email:
        return RedirectResponse("/login", status_code=303)

    try:
        oid = ObjectId(entry_id)
    except Exception:
        flash(request, "Invalid authenticator reference.", "danger")
        return RedirectResponse("/dashboard", status_code=303)

    result = totp_entries_collection.delete_one({"_id": oid, "user_email": email})
    if result.deleted_count:
        flash(request, "Authenticator removed.", "success")
    else:
        flash(request, "Authenticator not found.", "warning")
    return RedirectResponse("/dashboard", status_code=303)


@router.get("/api/totp/codes")
async def get_codes(request: Request):
    email = _current_user_email(request)
    if not email:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    now = time.time()
    codes: dict[str, str] = {}
    meta: dict[str, dict] = {}

    for entry in totp_entries_collection.find({"user_email": email}):
        eid = str(entry["_id"])
        period = int(entry.get("period", 30))
        try:
            totp = make_totp(
                entry["secret"],
                digits=int(entry.get("digits", 6)),
                period=period,
                algorithm=entry.get("algorithm", "SHA1"),
            )
            code = totp.now()
        except Exception:
            code = "------"
        remaining = period - (int(now) % period)
        codes[eid] = code
        meta[eid] = {"remaining": remaining, "period": period}

    return JSONResponse({"codes": codes, "meta": meta})