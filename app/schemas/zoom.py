from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ZoomWebhookPayload(BaseModel):
    """Permissive shape for Zoom webhooks with a required event type."""

    model_config = ConfigDict(extra="allow")

    event: str
    payload: dict = Field(default_factory=dict)
    event_ts: int | None = None


# ---------------------------------------------------------------------------
# Plain-text message models (existing)
# ---------------------------------------------------------------------------

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
    thread_id: str | None = None


# ---------------------------------------------------------------------------
# Rich interactive card models (native Zoom Team Chat chatbot format)
# ---------------------------------------------------------------------------

class ZoomRichSubHead(BaseModel):
    text: str


class ZoomRichHeadStyle(BaseModel):
    bold: bool = True


class ZoomRichHead(BaseModel):
    text: str
    style: ZoomRichHeadStyle | None = None
    sub_head: ZoomRichSubHead | None = None


class ZoomRichContent(BaseModel):
    """Zoom Team Chat chatbot interactive message content.

    ``body`` items are kept as plain dicts to accommodate the variety of
    element types (section, plain_text_input, fields, actions, message) that
    Zoom accepts without needing a complex discriminated-union hierarchy.
    """

    model_config = ConfigDict(extra="allow")

    head: ZoomRichHead
    body: list[dict] = Field(default_factory=list)
    settings: dict = Field(default_factory=dict)


class ZoomRichMessageRequest(BaseModel):
    robot_jid: str
    to_jid: str
    account_id: str
    user_jid: str
    is_markdown_support: bool = False
    content: ZoomRichContent
    thread_id: str | None = None