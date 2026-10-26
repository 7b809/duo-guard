# core/db.py
from pymongo import MongoClient

from core.config import settings

client = MongoClient(settings.MONGO_URL, serverSelectionTimeoutMS=5000)
database = client[settings.DB_NAME]
users_collection = database["users"]
totp_entries_collection = database["totp_entries"]


def init_db() -> None:
    """Create the indexes required by the application."""
    users_collection.create_index("email", unique=True)
    users_collection.create_index("api_key_hash", unique=True, sparse=True)
    totp_entries_collection.create_index("user_email")
    totp_entries_collection.create_index([("user_email", 1), ("secret", 1)])