from app.core.config import Settings
from app.services.zoom_chat_service import ZoomChatService


def test_zoom_message_payload_creation() -> None:
    settings = Settings(ZOOM_BOT_JID="bot@xmpp.zoom.us", ZOOM_ACCOUNT_ID="acc1")
    service = ZoomChatService(settings=settings)

    payload = service.build_message_payload(
        to_jid="user@xmpp.zoom.us",
        text="hello",
        user_jid="user@xmpp.zoom.us",
        thread_id="thread-1",
    )

    dumped = payload.model_dump(exclude_none=True)
    assert dumped["robot_jid"] == "bot@xmpp.zoom.us"
    assert dumped["to_jid"] == "user@xmpp.zoom.us"
    assert dumped["thread_id"] == "thread-1"
    assert dumped["content"]["body"][0]["text"] == "hello"


def test_zoom_rich_message_payload_creation() -> None:
    """build_rich_message_payload should assemble a ZoomRichMessageRequest with
    the correct head, sub_head, and body elements from a pre-built content dict."""
    settings = Settings(ZOOM_BOT_JID="bot@xmpp.zoom.us", ZOOM_ACCOUNT_ID="acc1")
    service = ZoomChatService(settings=settings)

    content = {
        "head": {
            "text": "We need a bit more information",
            "sub_head": {"text": "Please answer the questions below:"},
        },
        "body": [
            {"type": "plain_text_input", "action_id": "userClarificationResponse",
             "text": "Your answers", "value": "", "placeholder": "Answer here...",
             "multiline": True, "min_length": 0, "max_length": 2000},
            {"type": "section", "layout": "horizontal",
             "sections": [
                 {"type": "message", "text": "Press Submit when ready"},
                 {"type": "actions", "items": [{"text": "Submit details", "value": "{\"zoom_action\":\"submit_card\"}"}]},
             ]},
        ],
    }

    payload = service.build_rich_message_payload(
        to_jid="user@xmpp.zoom.us",
        content=content,
        user_jid="user@xmpp.zoom.us",
    )

    dumped = payload.model_dump(exclude_none=True)
    assert dumped["robot_jid"] == "bot@xmpp.zoom.us"
    assert dumped["to_jid"] == "user@xmpp.zoom.us"
    assert dumped["is_markdown_support"] is False  # rich messages use native format
    assert dumped["content"]["head"]["text"] == "We need a bit more information"
    assert dumped["content"]["head"]["sub_head"]["text"] == "Please answer the questions below:"
    assert len(dumped["content"]["body"]) == 2
    assert dumped["content"]["body"][0]["type"] == "plain_text_input"
