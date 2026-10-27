# routers/auth.py
from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from core.db import users_collection
from core.security import hash_password, verify_password
from core.security_monitor import (
    get_client_ip,
    is_ip_blocked,
    record_login_failure,
    record_login_success,
)
from core.templating import flash, render

router = APIRouter()


def _user_agent(request: Request) -> str:
    try:
        return request.headers.get("user-agent", "") or ""
    except Exception:
        return ""


@router.get("/register")
async def register_page(request: Request):
    if request.session.get("user_email"):
        return RedirectResponse("/dashboard", status_code=303)

    if is_ip_blocked(get_client_ip(request)):
        flash(
            request,
            "Too many failed attempts from this IP. Please try again later.",
            "danger",
        )
        return RedirectResponse("/login", status_code=303)

    return render(request, "register.html")


@router.post("/register")
async def register(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
):
    if is_ip_blocked(get_client_ip(request)):
        flash(
            request,
            "Too many failed attempts from this IP. Please try again later.",
            "danger",
        )
        return RedirectResponse("/login", status_code=303)

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
    ip = get_client_ip(request)
    ua = _user_agent(request)

    # Blocked IPs cannot even attempt a login
    if is_ip_blocked(ip):
        flash(
            request,
            "Too many failed attempts from this IP. Please try again later.",
            "danger",
        )
        return RedirectResponse("/login", status_code=303)

    email_clean = email.strip().lower()
    user = users_collection.find_one({"email": email_clean})

    if not user or not verify_password(password, user.get("password_hash", "")):
        result = record_login_failure(ip, email_clean, ua)
        if result.get("blocked"):
            flash(
                request,
                "Too many failed attempts. This IP has been temporarily "
                "blocked.",
                "danger",
            )
        else:
            flash(request, "Invalid email or password.", "danger")
        return RedirectResponse("/login", status_code=303)

    record_login_success(ip, user["email"])
    request.session["user_email"] = user["email"]
    flash(request, "Welcome back!", "success")
    return RedirectResponse("/dashboard", status_code=303)


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    flash(request, "You have been signed out.", "info")
    return RedirectResponse("/login", status_code=303)