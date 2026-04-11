from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.core.config import Settings
from app.models.conversation_mapping import ConversationMapping
from app.schemas.directline import DirectLineTokenResponse
from app.services.copilot_token_service import CopilotTokenService
from app.services.directline_service import DirectLineService


class StubTokenService(CopilotTokenService):
    def __init__(self):
        pass

    async def get_directline_token(self) -> DirectLineTokenResponse:
        return DirectLineTokenResponse(token="fresh", conversationId="new-conv", expires_in=1800)

    @staticmethod
    def compute_expiry(expires_in_seconds: int):
        return datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)


@pytest.mark.asyncio
async def test_maybe_refresh_mapping_updates_expired_token() -> None:
    settings = Settings(DIRECTLINE_API_BASE="https://directline.botframework.com/v3/directline")
    service = DirectLineService(settings=settings, client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: None)))

    mapping = ConversationMapping(
        zoom_user_id="u",
        zoom_channel_id="c",
        zoom_thread_id="t",
        zoom_to_jid="jid",
        directline_conversation_id="old",
        directline_token="old-token",
        directline_token_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        last_activity_at=datetime.now(timezone.utc),
        status="active",
    )

    refreshed = await service.maybe_refresh_mapping(mapping, StubTokenService())
    assert refreshed.directline_token == "fresh"
    assert refreshed.directline_conversation_id == "new-conv"


@pytest.mark.asyncio
async def test_send_and_poll_directline() -> None:
    events = [
        {
            "activities": [
                {"id": "1", "type": "message", "from": {"id": "zoom:u1"}, "text": "hello"},
                {"id": "2", "type": "message", "from": {"id": "bot"}, "text": "hi there"},
            ],
            "watermark": "2",
        },
        {
            "activities": [],
            "watermark": "2",
        },
    ]
    calls = {"get": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and "activities" in str(request.url):
            return httpx.Response(200, json={"id": "activity-id"})
        if request.method == "GET" and "activities" in str(request.url):
            idx = calls["get"]
            calls["get"] += 1
            return httpx.Response(200, json=events[idx])
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    settings = Settings(POLL_INTERVAL_SECONDS=0.01, POLL_TIMEOUT_SECONDS=2)
    service = DirectLineService(settings=settings, client=client)

    activity_id = await service.send_message(
        conversation_id="conv1",
        token="token",
        text="hello",
        from_id="zoom:u1",
        locale="en-US",
        metadata={"source": "zoom-team-chat"},
    )
    assert activity_id == "activity-id"

    messages, watermark = await service.poll_for_bot_reply(
        conversation_id="conv1",
        token="token",
        watermark=None,
        user_from_id="zoom:u1",
    )
    assert messages == ["hi there"]
    assert watermark == "2"

    await client.aclose()
