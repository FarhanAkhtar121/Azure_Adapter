from app.core.config import Settings
from app.services.zoom_chat_service import ZoomChatService


def test_zoom_message_payload_creation() -> None:
    settings = Settings(ZOOM_ACCOUNT_ID="acc1")
    service = ZoomChatService(settings=settings)

    payload = service.build_message_payload(
        to_jid="user@xmpp.zoom.us",
        text="hello",
        thread_id="thread-1",
        channel_id="channel-1",
    )

    dumped = payload.model_dump(exclude_none=True)
    assert dumped["message"] == "hello"
    assert dumped["to_channel"] == "channel-1"
    assert dumped["reply_main_message_id"] == "thread-1"


def test_zoom_direct_message_payload_creation_uses_contact_fallback() -> None:
    settings = Settings(ZOOM_ACCOUNT_ID="acc1")
    service = ZoomChatService(settings=settings)

    payload = service.build_message_payload(
        to_jid="user@xmpp.zoom.us",
        text="hello",
        thread_id="root",
        user_jid="member-1",
    )

    dumped = payload.model_dump(exclude_none=True)
    assert dumped["message"] == "hello"
    assert dumped["to_contact"] == "member-1"
    assert "reply_main_message_id" not in dumped
