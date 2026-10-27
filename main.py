# main.py
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from core.config import settings
from core.db import init_db
from core.platforms import all_platforms
from core.runtime_config import ensure_platform_registered
from routers import admin, api, apikeys, auth, pages, totp
from scheduler.jobs import (
    check_startup_token_refresh,
    start_scheduler,
    stop_scheduler,
    sync_scheduler_from_runtime,
)
from services.telegram_bot import start_bot, stop_bot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
)

log = logging.getLogger("duo-guard.startup")


def _seed_runtime_platforms() -> None:
    """
    Ensure every platform declared in .env has a runtime entry.

    Existing entries are preserved — this only inserts defaults for
    platforms that don't have a runtime override yet.
    """
    for platform in all_platforms():
        try:
            ensure_platform_registered(
                platform.name, platform.auto_refresh, platform.on_boot
            )
        except Exception as exc:
            log.warning(
                "Failed to seed runtime config for %s: %s",
                platform.name,
                exc,
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()

    # 1) Make sure every .env platform exists in runtime_config
    _seed_runtime_platforms()

    # 2) Start the scheduler (respects runtime pause state)
    start_scheduler()
    sync_scheduler_from_runtime()

    # 3) Startup token check (respects on_boot flags from runtime config)
    try:
        check_startup_token_refresh()
    except Exception as exc:
        log.warning("Startup token refresh check failed: %s", exc)

    # 4) Start the interactive Telegram bot
    start_bot()

    try:
        yield
    finally:
        stop_bot()
        stop_scheduler()


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    session_cookie="duo_guard_session",
    max_age=60 * 60 * 8,
    same_site="lax",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(admin.router)
app.include_router(api.router)
app.include_router(apikeys.router)
app.include_router(pages.router)
app.include_router(auth.router)
app.include_router(totp.router)