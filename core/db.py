# core/db.py
from pymongo import MongoClient

from core.config import settings

client = MongoClient(settings.MONGO_URL, serverSelectionTimeoutMS=5000)

# ---- DuoGuard's own database ----
database = client[settings.DB_NAME]
users_collection = database["users"]
totp_entries_collection = database["totp_entries"]
push_logs_collection = database["push_logs"]

# ---- Security monitoring collections ----
login_attempts_collection = database["login_attempts"]
api_failures_collection = database["api_failures"]
blocked_ips_collection = database["blocked_ips"]

# ---- Runtime state (bot toggles, scheduler pause state) ----
runtime_config_collection = database["runtime_config"]
bot_state_collection = database["bot_state"]

# ---- Upstox dashboard's own database ----
upstox_app_database = client[settings.UPSTOX_APP_DB_NAME]
upstox_app_tokens_collection = upstox_app_database[
    settings.UPSTOX_APP_TOKENS_COLLECTION
]


def init_db() -> None:
    """Create the indexes required by the application."""
    users_collection.create_index("email", unique=True)
    users_collection.create_index("api_key_hash", unique=True, sparse=True)
    totp_entries_collection.create_index("user_email")
    totp_entries_collection.create_index([("user_email", 1), ("secret", 1)])
    totp_entries_collection.create_index("issuer")
    push_logs_collection.create_index("started_at")

    login_attempts_collection.create_index([("ip", 1), ("timestamp", -1)])
    login_attempts_collection.create_index([("email", 1), ("timestamp", -1)])
    login_attempts_collection.create_index(
        "timestamp", expireAfterSeconds=86400
    )
    api_failures_collection.create_index([("ip", 1), ("timestamp", -1)])
    api_failures_collection.create_index(
        "timestamp", expireAfterSeconds=86400
    )
    blocked_ips_collection.create_index("ip", unique=True)
    blocked_ips_collection.create_index(
        "blocked_until", expireAfterSeconds=0
    )