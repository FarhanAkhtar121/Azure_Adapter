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


class DirectLineActivity(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    type: str
    from_: DirectLineMessageFrom | None = Field(default=None, alias="from")
    text: str | None = None
    speak: str | None = None
    attachments: list[DirectLineAttachment] = Field(default_factory=list)


class DirectLineActivitiesResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    activities: list[DirectLineActivity] = Field(default_factory=list)
    watermark: str | None = None