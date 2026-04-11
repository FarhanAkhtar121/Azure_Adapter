from app.core.config import Settings
from app.services.zoom_chat_service import ZoomChatService


def test_zoom_message_payload_creation() -> None:
    settings = Settings(ZOOM_BOT_JID="bot@xmpp.zoom.us", ZOOM_ACCOUNT_ID="acc1")
    service = ZoomChatService(settings=settings)

    payload = service.build_message_payload(
        to_jid="user@xmpp.zoom.us",
        text="hello",
        thread_id="thread-1",
    )

    dumped = payload.model_dump(exclude_none=True)
    assert dumped["robot_jid"] == "bot@xmpp.zoom.us"
    assert dumped["to_jid"] == "user@xmpp.zoom.us"
    assert dumped["thread_id"] == "thread-1"
    assert dumped["content"]["body"][0]["text"] == "hello"
