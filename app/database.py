"""Explicit SQLAlchemy engine creation; importing this module does not connect."""

from sqlalchemy import Engine, create_engine

from app.config import Settings


def build_engine(settings: Settings) -> Engine:
    """Prepare a PostgreSQL engine; the first connection is opened on use."""
    if not settings.database_url:
        raise ValueError("Set DATABASE_URL in .env before using PostgreSQL.")
    return create_engine(settings.database_url, pool_pre_ping=True)
