# main.py
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from core.config import settings
from core.db import init_db
from routers import api, apikeys, auth, pages, totp


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    session_cookie="duo_guard_session",
    max_age=60 * 60 * 8,
    same_site="lax",
)

# The /api/v1 endpoints are key-authenticated (no cookies), so wildcard
# origins are safe here. Restrict this list in production if you like.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(api.router)
app.include_router(apikeys.router)
app.include_router(pages.router)
app.include_router(auth.router)
app.include_router(totp.router)