from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ZoomWebhookPayload(BaseModel):
    """Permissive shape for Zoom webhooks with a required event type."""

    model_config = ConfigDict(extra="allow")

    event: str
    payload: dict = Field(default_factory=dict)
    event_ts: int | None = None


class ZoomReplyBodyItem(BaseModel):
    type: str = "message"
    text: str


class ZoomReplyContent(BaseModel):
    head: dict
    body: list[ZoomReplyBodyItem]


class ZoomChatMessageRequest(BaseModel):
    robot_jid: str
    to_jid: str
    account_id: str | None = None
    user_jid: str | None = None
    thread_id: str | None = None
    content: ZoomReplyContent
