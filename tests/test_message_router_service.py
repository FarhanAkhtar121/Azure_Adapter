from app.core.config import Settings
from app.services.message_router_service import MessageRouterService


def test_normalize_bot_notification_payload() -> None:
    router = MessageRouterService(Settings(ZOOM_BOT_JID="bot@xmpp.zoom.us"))
    payload = {
        "event": "bot_notification",
        "event_ts": 1710000000,
        "payload": {
            "cmd": "hello world",
            "user_id": "u1",
            "channel_id": "c1",
            "thread_id": "t1",
            "to_jid": "tojid",
            "locale": "en-US",
        },
    }

    normalized = router.normalize(payload)

    assert normalized is not None
    assert normalized.user_text == "hello world"
    assert normalized.zoom_user_id == "u1"
    assert normalized.zoom_channel_id == "c1"
    assert normalized.zoom_thread_id == "t1"
    assert normalized.reply_target.to_jid == "tojid"


def test_unsupported_event_is_ignored() -> None:
    router = MessageRouterService(Settings())
    payload = {"event": "unsupported_event", "payload": {"cmd": "hello"}}

    assert router.normalize(payload) is None
