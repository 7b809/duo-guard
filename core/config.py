# core/config.py
import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    MONGO_URL: str = os.getenv("MONGO_URL", "mongodb://localhost:27017")
    DB_NAME: str = os.getenv("DB_NAME", "duo_auth")
    SECRET_KEY: str = os.getenv("SECRET_KEY", "change-me-in-env")
    APP_NAME: str = os.getenv("APP_NAME", "DuoGuard")


settings = Settings()