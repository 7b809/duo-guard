# core/db.py
from pymongo import MongoClient

from core.config import settings

client = MongoClient(settings.MONGO_URL, serverSelectionTimeoutMS=5000)

# ---- DuoGuard's own database ----
database = client[settings.DB_NAME]
users_collection = database["users"]
totp_entries_collection = database["totp_entries"]
push_logs_collection = database["push_logs"]

# ---- Upstox dashboard's own database (read-only here) ----
# Used purely to detect whether today's token has already been refreshed.
upstox_app_database = client[settings.UPSTOX_APP_DB_NAME]
upstox_app_tokens_collection = upstox_app_database[settings.UPSTOX_APP_TOKENS_COLLECTION]


def init_db() -> None:
    """Create the indexes required by the application."""
    users_collection.create_index("email", unique=True)
    users_collection.create_index("api_key_hash", unique=True, sparse=True)
    totp_entries_collection.create_index("user_email")
    totp_entries_collection.create_index([("user_email", 1), ("secret", 1)])
    totp_entries_collection.create_index("issuer")
    push_logs_collection.create_index("started_at")