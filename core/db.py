# core/db.py
from pymongo import MongoClient

from core.config import settings

client = MongoClient(settings.MONGO_URL, serverSelectionTimeoutMS=5000)
database = client[settings.DB_NAME]
users_collection = database["users"]


def init_db() -> None:
    """Create the indexes required by the application."""
    users_collection.create_index("email", unique=True)