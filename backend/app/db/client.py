"""
MongoDB connection + collection accessors.

We use Motor (async) so FastAPI routes can await DB calls. A single
client is created in the FastAPI lifespan and shared across requests.

Collection helpers (users, festivals, cert_templates, runs) exist as
properties to keep callers from typing collection names by hand.
"""

from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection, AsyncIOMotorDatabase
from pymongo import ASCENDING

from backend.app.settings import get_settings


class MongoDB:
    """Wrapper holding the client and exposing typed collection accessors."""

    client: AsyncIOMotorClient | None = None
    db: AsyncIOMotorDatabase | None = None

    @classmethod
    async def connect(cls) -> None:
        settings = get_settings()
        cls.client = AsyncIOMotorClient(settings.MONGO_URI)
        cls.db = cls.client[settings.MONGO_DB]
        await cls._ensure_indexes()

    @classmethod
    async def disconnect(cls) -> None:
        if cls.client is not None:
            cls.client.close()
            cls.client = None
            cls.db = None

    # ── Collections ──────────────────────────────────────────────────────────

    @classmethod
    def users(cls) -> AsyncIOMotorCollection:
        return cls.db["users"]

    @classmethod
    def festivals(cls) -> AsyncIOMotorCollection:
        return cls.db["festivals"]

    @classmethod
    def cert_templates(cls) -> AsyncIOMotorCollection:
        return cls.db["cert_templates"]

    @classmethod
    def runs(cls) -> AsyncIOMotorCollection:
        return cls.db["runs"]

    # ── Indexes ──────────────────────────────────────────────────────────────

    @classmethod
    async def _ensure_indexes(cls) -> None:
        """Create indexes on first connect. Idempotent — Mongo skips existing ones."""
        await cls.users().create_index([("email", ASCENDING)], unique=True)

        await cls.festivals().create_index([("slug", ASCENDING)], unique=True)

        # One template doc per (festival, category, status)
        await cls.cert_templates().create_index(
            [("festival_id", ASCENDING),
             ("category", ASCENDING),
             ("judging_status", ASCENDING)],
            unique=True,
        )

        await cls.runs().create_index([("festival_id", ASCENDING),
                                        ("started_at", ASCENDING)])


def get_db() -> AsyncIOMotorDatabase:
    """FastAPI dependency — raises if connect() hasn't run yet."""
    if MongoDB.db is None:
        raise RuntimeError("MongoDB not connected. Did the FastAPI lifespan run?")
    return MongoDB.db
