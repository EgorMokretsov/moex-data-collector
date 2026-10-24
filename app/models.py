"""ORM base. Tables will be defined after inspecting real MOEX archive fields."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Common SQLAlchemy base for future database models."""
