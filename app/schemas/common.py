from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ReplyTarget(BaseModel):
    to_jid: str
    user_jid: str | None = None
    account_id: str | None = None
    thread_id: str | None = None
    channel_id: str | None = None


class NormalizedInboundMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    event_type: str
    event_id: str | None = None
    user_text: str
    zoom_user_id: str
    zoom_channel_id: str
    zoom_thread_id: str
    locale: str | None = None
    reply_target: ReplyTarget
    occurred_at: datetime | None = None
    raw_metadata: dict = Field(default_factory=dict)


class AckResponse(BaseModel):
    status: str