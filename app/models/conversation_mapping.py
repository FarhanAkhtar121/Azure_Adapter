from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.db import Base


class ConversationMapping(Base):
    __tablename__ = "conversation_mappings"
    __table_args__ = (
        UniqueConstraint(
            "zoom_user_id",
            "zoom_channel_id",
            "zoom_thread_id",
            name="uq_zoom_context",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    zoom_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    zoom_channel_id: Mapped[str] = mapped_column(String(128), nullable=False)
    zoom_thread_id: Mapped[str] = mapped_column(String(128), nullable=False)
    zoom_to_jid: Mapped[str | None] = mapped_column(String(256), nullable=True)

    directline_conversation_id: Mapped[str] = mapped_column(String(256), nullable=False)
    directline_token: Mapped[str] = mapped_column(Text, nullable=False)
    directline_token_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    watermark: Mapped[str | None] = mapped_column(String(64), nullable=True)
    locale: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    last_zoom_event_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
