from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class DirectLineTokenResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    token: str
    conversation_id: str | None = Field(default=None, alias="conversationId")
    expires_in: int = Field(default=1800, alias="expires_in")


class DirectLineMessageFrom(BaseModel):
    id: str


class DirectLineAttachment(BaseModel):
    model_config = ConfigDict(extra="allow")

    content_type: str | None = Field(default=None, alias="contentType")
    content: dict | None = None
    name: str | None = None


class DirectLineCardAction(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str | None = None
    title: str | None = None
    value: str | None = None
    text: str | None = None


class DirectLineSuggestedActions(BaseModel):
    model_config = ConfigDict(extra="allow")

    actions: list[DirectLineCardAction] = Field(default_factory=list)


class DirectLineActivity(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    type: str
    from_: DirectLineMessageFrom | None = Field(default=None, alias="from")
    text: str | None = None
    speak: str | None = None
    attachments: list[DirectLineAttachment] = Field(default_factory=list)
    suggested_actions: DirectLineSuggestedActions | None = Field(default=None, alias="suggestedActions")


class DirectLineActivitiesResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    activities: list[DirectLineActivity] = Field(default_factory=list)
    watermark: str | None = None