from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class RequestLog(Base):
    """
    Immutable record of every request that passed through the gateway.

    S — pure data; written once, never mutated.
    """
    __tablename__ = "request_logs"

    id:          Mapped[int]   = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_ip:   Mapped[str]   = mapped_column(String(50), nullable=False)
    method:      Mapped[str]   = mapped_column(String(10), nullable=False)
    path:        Mapped[str]   = mapped_column(String(500), nullable=False)
    upstream:    Mapped[str]   = mapped_column(String(500), nullable=False)
    status_code: Mapped[int]   = mapped_column(Integer, nullable=False)
    latency_ms:  Mapped[float] = mapped_column(Float, nullable=False)
    blocked:     Mapped[bool]  = mapped_column(Boolean, default=False)
    blocked_by:  Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at:  Mapped[datetime]   = mapped_column(DateTime, default=datetime.utcnow)
