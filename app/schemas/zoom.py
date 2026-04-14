from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ZoomWebhookPayload(BaseModel):
    """Permissive shape for Zoom webhooks with a required event type."""

    model_config = ConfigDict(extra="allow")

    event: str
    payload: dict = Field(default_factory=dict)
    event_ts: int | None = None


class ZoomReplyHead(BaseModel):
    text: str


class ZoomReplyBodyItem(BaseModel):
    type: str = "message"
    text: str


class ZoomReplyContent(BaseModel):
    head: ZoomReplyHead
    body: list[ZoomReplyBodyItem]


class ZoomChatMessageRequest(BaseModel):
    robot_jid: str
    to_jid: str
    account_id: str
    user_jid: str
    is_markdown_support: bool = True
    content: ZoomReplyContent