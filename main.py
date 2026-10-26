# main.py
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from core.config import settings
from core.db import init_db
from routers import admin, api, apikeys, auth, pages, totp
from scheduler.jobs import (
    check_startup_token_refresh,
    start_scheduler,
    stop_scheduler,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()

    # Startup token check:
    #   - Sends a Telegram message describing the MongoDB token status.
    #   - If the project started after 04:00 IST and the token is stale,
    #     triggers a fresh push in the background (which sends its own
    #     final-result Telegram message).
    try:
        check_startup_token_refresh()
    except Exception as exc:
        logging.getLogger("duo-guard.startup").warning(
            "Startup token refresh check failed: %s", exc
        )

    try:
        yield
    finally:
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