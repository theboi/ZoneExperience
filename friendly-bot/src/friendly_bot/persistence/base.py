"""Shared SQLAlchemy metadata registry for all F01 durable records."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """The single metadata registry owned by Friendly Bot F01."""
