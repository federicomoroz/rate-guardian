from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

KEY_TYPES = ("ip", "global")


class Rule(Base):
    """
    A rate-limiting rule applied to requests whose path matches path_pattern.

    S — pure data; no logic, no I/O.
    """
    __tablename__ = "rules"

    id:             Mapped[int]  = mapped_column(Integer, primary_key=True, autoincrement=True)
    name:           Mapped[str]  = mapped_column(String(100), nullable=False)
    path_pattern:   Mapped[str]  = mapped_column(String(255), nullable=False)
    limit:          Mapped[int]  = mapped_column(Integer, nullable=False)
    window_seconds: Mapped[int]  = mapped_column(Integer, nullable=False)
    key_type:       Mapped[str]  = mapped_column(String(20), nullable=False, default="ip")
    active:         Mapped[bool] = mapped_column(Boolean, default=True)
    created_at:     Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
