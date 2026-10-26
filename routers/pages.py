# routers/pages.py
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from core.db import users_collection
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

    return render(request, "dashboard.html", {"user": user})