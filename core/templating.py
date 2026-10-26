# core/templating.py
from fastapi import Request
from fastapi.templating import Jinja2Templates

from core.config import settings

templates = Jinja2Templates(directory="templates")


def flash(request: Request, message: str, category: str = "info") -> None:
    request.session.setdefault("flashes", []).append(
        {"message": message, "category": category}
    )


def render(request: Request, template_name: str, context: dict | None = None):
    ctx = {
        "app_name": settings.APP_NAME,
        "user_email": request.session.get("user_email"),
        "flashes": request.session.pop("flashes", []),
        # A fresh API key is shown exactly once (right after regeneration).
        "new_api_key": request.session.pop("new_api_key", None),
    }
    if context:
        ctx.update(context)
    return templates.TemplateResponse(request, template_name, ctx)