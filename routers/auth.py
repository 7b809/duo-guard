# routers/auth.py
from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from core.db import users_collection
from core.security import hash_password, verify_password, verify_totp_code
from core.templating import flash, render

router = APIRouter()


@router.get("/register")
async def register_page(request: Request):
    if request.session.get("user_email"):
        return RedirectResponse("/dashboard", status_code=303)
    return render(request, "register.html")


@router.post("/register")
async def register(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
):
    email = email.strip().lower()

    if "@" not in email or len(email) < 5:
        flash(request, "Please enter a valid email address.", "danger")
        return RedirectResponse("/register", status_code=303)

    if len(password) < 8:
        flash(request, "Password must be at least 8 characters long.", "danger")
        return RedirectResponse("/register", status_code=303)

    if password != confirm_password:
        flash(request, "Passwords do not match.", "danger")
        return RedirectResponse("/register", status_code=303)

    if users_collection.find_one({"email": email}):
        flash(request, "An account with this email already exists.", "danger")
        return RedirectResponse("/register", status_code=303)

    users_collection.insert_one(
        {
            "email": email,
            "password_hash": hash_password(password),
            "totp_secret": None,
            "totp_enabled": False,
        }
    )

    flash(request, "Account created successfully. Please sign in.", "success")
    return RedirectResponse("/login", status_code=303)


@router.get("/login")
async def login_page(request: Request):
    if request.session.get("user_email"):
        return RedirectResponse("/dashboard", status_code=303)
    return render(request, "login.html")


@router.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
):
    user = users_collection.find_one({"email": email.strip().lower()})

    if not user or not verify_password(password, user.get("password_hash", "")):
        flash(request, "Invalid email or password.", "danger")
        return RedirectResponse("/login", status_code=303)

    if user.get("totp_enabled") and user.get("totp_secret"):
        request.session["pending_2fa_email"] = user["email"]
        return RedirectResponse("/verify", status_code=303)

    request.session["user_email"] = user["email"]
    flash(request, "Welcome back!", "success")
    return RedirectResponse("/dashboard", status_code=303)


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    flash(request, "You have been signed out.", "info")
    return RedirectResponse("/login", status_code=303)


@router.get("/verify")
async def verify_page(request: Request):
    if not request.session.get("pending_2fa_email"):
        return RedirectResponse("/login", status_code=303)
    return render(request, "verify.html")


@router.post("/verify")
async def verify(request: Request, code: str = Form(...)):
    email = request.session.get("pending_2fa_email")
    if not email:
        return RedirectResponse("/login", status_code=303)

    user = users_collection.find_one({"email": email})
    if not user or not user.get("totp_secret"):
        request.session.pop("pending_2fa_email", None)
        flash(request, "Something went wrong. Please sign in again.", "danger")
        return RedirectResponse("/login", status_code=303)

    if verify_totp_code(user["totp_secret"], code.strip()):
        request.session.pop("pending_2fa_email", None)
        request.session["user_email"] = email
        flash(request, "Two-factor authentication successful.", "success")
        return RedirectResponse("/dashboard", status_code=303)

    flash(request, "Invalid or expired code. Please try again.", "danger")
    return RedirectResponse("/verify", status_code=303)