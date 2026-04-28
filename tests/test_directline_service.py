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

    messages, zoom_cards, watermark, pending_inputs = await service.poll_for_bot_reply(
        conversation_id="conv1",
        token="token",
        watermark=None,
        user_from_id="zoom:u1",
    )
    assert messages == ["hi there"]
    assert watermark == "2"
    assert pending_inputs == []
    assert zoom_cards == [None]

    await client.aclose()


@pytest.mark.asyncio
async def test_poll_extracts_adaptive_card_fallback_text() -> None:
    """When Copilot Studio returns an adaptive card with no text field, poll_for_bot_reply
    should extract the card body TextBlocks and FactSet entries as plain text."""
    adaptive_card_activity = {
        "id": "10",
        "type": "message",
        "from": {"id": "bot"},
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "type": "AdaptiveCard",
                    "body": [
                        {"type": "TextBlock", "text": "Your ticket has been created."},
                        {
                            "type": "FactSet",
                            "facts": [
                                {"title": "Ticket ID", "value": "INC001"},
                                {"title": "Status", "value": "Open"},
                            ],
                        },
                    ],
                },
            }
        ],
    }
    events = [
        {"activities": [adaptive_card_activity], "watermark": "1"},
        {"activities": [], "watermark": "1"},
    ]
    calls = {"get": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "activities" in str(request.url):
            idx = calls["get"]
            calls["get"] += 1
            return httpx.Response(200, json=events[idx])
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = Settings(POLL_INTERVAL_SECONDS=0.01, POLL_TIMEOUT_SECONDS=2, DEBUG_TRANSCRIPT_LOGGING=True)
    service = DirectLineService(settings=settings, client=client)

    messages, zoom_cards, watermark, pending_inputs = await service.poll_for_bot_reply(
        conversation_id="conv1",
        token="token",
        watermark=None,
        user_from_id="zoom:u1",
    )

    assert messages == ["Your ticket has been created.\nTicket ID: INC001\nStatus: Open"]
    assert watermark == "1"
    assert pending_inputs == []
    # FactSet content now maps to a Zoom fields section.
    assert zoom_cards[0] is not None
    assert zoom_cards[0]["head"]["text"] == "Your ticket has been created."

    await client.aclose()


@pytest.mark.asyncio
async def test_poll_extracts_hero_card_text() -> None:
    """Hero card with title + subtitle + text should be rendered as joined plain text."""
    hero_card_activity = {
        "id": "20",
        "type": "message",
        "from": {"id": "bot"},
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.hero",
                "content": {
                    "title": "Ticket Created",
                    "subtitle": "INC001",
                    "text": "Your request has been logged.",
                },
            }
        ],
    }
    events = [
        {"activities": [hero_card_activity], "watermark": "1"},
        {"activities": [], "watermark": "1"},
    ]
    calls = {"get": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "activities" in str(request.url):
            idx = calls["get"]
            calls["get"] += 1
            return httpx.Response(200, json=events[idx])
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = Settings(POLL_INTERVAL_SECONDS=0.01, POLL_TIMEOUT_SECONDS=2)
    service = DirectLineService(settings=settings, client=client)

    messages, zoom_cards, watermark, pending_inputs = await service.poll_for_bot_reply(
        conversation_id="conv1",
        token="token",
        watermark=None,
        user_from_id="zoom:u1",
    )

    assert messages == ["Ticket Created\nINC001\nYour request has been logged."]
    assert watermark == "1"
    assert pending_inputs == []
    assert zoom_cards == [None]

    await client.aclose()


@pytest.mark.asyncio
async def test_poll_detects_adaptive_card_inputs_for_submit() -> None:
    card_with_inputs = {
        "id": "30", "type": "message", "from": {"id": "bot"},
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "type": "AdaptiveCard",
                "body": [
                    {"type": "TextBlock", "text": "Please answer the following questions:"},
                    {"type": "Input.Text", "id": "userAnswers", "isMultiline": True},
                ],
                "actions": [
                    {
                        "type": "Action.Submit",
                        "title": "Submit details",
                        "data": {"ticketStage": "clarification", "flowId": "abc123"},
                    }
                ],
            },
        }],
    }
    events = [
        {"activities": [card_with_inputs], "watermark": "1"},
        {"activities": [], "watermark": "1"},
    ]
    calls = {"get": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "activities" in str(request.url):
            idx = calls["get"]; calls["get"] += 1
            return httpx.Response(200, json=events[idx])
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = Settings(POLL_INTERVAL_SECONDS=0.01, POLL_TIMEOUT_SECONDS=2)
    service = DirectLineService(settings=settings, client=client)

    messages, zoom_cards, watermark, pending_inputs = await service.poll_for_bot_reply(
        conversation_id="conv1", token="token", watermark=None, user_from_id="zoom:u1",
    )

    assert messages == ["Please answer the following questions:"]
    assert pending_inputs == [
        {
            "id": "userAnswers",
            "type": "Input.Text",
            "label": None,
            "value": None,
            "placeholder": None,
            "isMultiline": True,
            "action_data": {"ticketStage": "clarification", "flowId": "abc123"},
        }
    ]
    # Card has inputs + Action.Submit → Zoom card should be built
    assert zoom_cards[0] is not None
    assert zoom_cards[0]["head"]["text"] == "Please answer the following questions:"
    await client.aclose()


@pytest.mark.asyncio
async def test_poll_renders_adaptive_card_input_values() -> None:
    card_with_editable_values = {
        "id": "40",
        "type": "message",
        "from": {"id": "bot"},
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "type": "AdaptiveCard",
                    "body": [
                        {"type": "TextBlock", "text": "Review and Edit Ticket Details"},
                        {
                            "type": "Input.Text",
                            "id": "confirmed_short_description",
                            "label": "Short Description",
                            "value": "Printer issue",
                        },
                        {
                            "type": "Input.Text",
                            "id": "confirmed_Category",
                            "label": "Category",
                            "value": "Hardware",
                        },
                    ],
                    "actions": [{"type": "Action.Submit", "title": "Confirm"}],
                },
            }
        ],
    }
    events = [
        {"activities": [card_with_editable_values], "watermark": "1"},
        {"activities": [], "watermark": "1"},
    ]
    calls = {"get": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "activities" in str(request.url):
            idx = calls["get"]
            calls["get"] += 1
            return httpx.Response(200, json=events[idx])
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = Settings(POLL_INTERVAL_SECONDS=0.01, POLL_TIMEOUT_SECONDS=2)
    service = DirectLineService(settings=settings, client=client)

    messages, zoom_cards, watermark, pending_inputs = await service.poll_for_bot_reply(
        conversation_id="conv1",
        token="token",
        watermark=None,
        user_from_id="zoom:u1",
    )

    assert messages == [
        "Review and Edit Ticket Details\nShort Description: Printer issue\nCategory: Hardware"
    ]
    assert watermark == "1"
    assert pending_inputs[0]["id"] == "confirmed_short_description"
    # Prefilled edit cards should render as editable-only (no duplicate display fields).
    card = zoom_cards[0]
    assert card is not None
    assert card["head"]["text"] == "Review and Edit Ticket Details"
    assert not any(
        section.get("type") == "section" and any(sub.get("type") == "fields" for sub in section.get("sections", []))
        for section in card["body"]
    )
    editable_inputs = [item for item in card["body"] if item.get("type") == "plain_text_input"]
    assert len(editable_inputs) == 2
    await client.aclose()


def test_build_zoom_card_content_submit_encodes_postback() -> None:
    """_build_zoom_card_content should produce a valid Zoom card dict with a JSON-encoded
    submit button value containing the card inputs and Action.Submit hidden data."""
    import json

    content = {
        "type": "AdaptiveCard",
        "body": [
            {"type": "TextBlock", "text": "🧐 We need a bit more information", "weight": "Bolder"},
            {"type": "TextBlock", "text": "Please answer the questions below:"},
            {"type": "Input.Text", "id": "userClarificationResponse", "label": "Your answers",
             "placeholder": "Answer each question...", "isMultiline": True},
        ],
        "actions": [
            {"type": "Action.Submit", "title": "Submit details",
             "data": {"ticketStage": "clarification"}},
        ],
    }
    card_inputs = DirectLineService._extract_card_inputs(content)
    result = DirectLineService._build_zoom_card_content(content, card_inputs)

    assert result is not None
    assert result["head"]["text"] == "🧐 We need a bit more information"
    assert result["head"]["sub_head"]["text"] == "Please answer the questions below:"

    # Should have a plain_text_input for the Input.Text field
    plain_input = next(item for item in result["body"] if item.get("type") == "plain_text_input")
    assert plain_input["action_id"] == "userClarificationResponse"
    assert plain_input["text"] == "Your answers"
    assert plain_input["placeholder"] == "Answer each question..."
    assert plain_input["multiline"] is True

    # Should have an actions section with a submit button
    action_section = next(
        item for item in result["body"]
        if item.get("type") == "section" and
        any(s.get("type") == "actions" for s in item.get("sections", []))
    )
    actions_subsection = next(s for s in action_section["sections"] if s.get("type") == "actions")
    button = actions_subsection["items"][0]
    assert button["text"] == "Submit details"

    button_value = json.loads(button["value"])
    assert button_value["zoom_action"] == "submit_card"
    assert button_value["action_data"] == {"ticketStage": "clarification"}
    assert "input_values" not in button_value


def test_build_zoom_card_content_open_url() -> None:
    """Action.OpenUrl should be rendered as a markdown link in a message section."""
    content = {
        "type": "AdaptiveCard",
        "body": [{"type": "TextBlock", "text": "View your ticket"}],
        "actions": [{"type": "Action.OpenUrl", "title": "Open Portal", "url": "https://portal.example.com"}],
    }
    result = DirectLineService._build_zoom_card_content(content, [])

    assert result is not None
    link_section = next(
        item for item in result["body"]
        if item.get("type") == "section" and
        any("Open Portal" in s.get("text", "") for s in item.get("sections", []))
    )
    link_text = next(s["text"] for s in link_section["sections"] if "Open Portal" in s.get("text", ""))
    assert "[Open Portal](https://portal.example.com)" in link_text


def test_build_zoom_card_content_returns_none_without_title() -> None:
    """Cards with no TextBlock body should not produce a Zoom card (fall through to text)."""
    content = {
        "type": "AdaptiveCard",
        "body": [
            {"type": "FactSet", "facts": [{"title": "Status", "value": "Open"}]},
        ],
    }
    result = DirectLineService._build_zoom_card_content(content, [])
    assert result is None
