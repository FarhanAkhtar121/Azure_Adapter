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


def test_direct_message_does_not_treat_jid_as_channel_id() -> None:
    router = MessageRouterService(Settings(ZOOM_BOT_JID="bot@xmpp.zoom.us"))
    payload = {
        "event": "bot_notification",
        "event_ts": 1710000000,
        "payload": {
            "cmd": "hello world",
            "user_id": "u1",
            "toJid": "u1@xmpp.zoom.us",
            "thread_id": "t1",
        },
    }

    normalized = router.normalize(payload)

    assert normalized is not None
    assert normalized.zoom_channel_id == "u1@xmpp.zoom.us"
    assert normalized.reply_target.to_jid == "u1@xmpp.zoom.us"
    assert normalized.reply_target.channel_id is None


def test_normalize_team_chat_dm_message_posted_payload() -> None:
    router = MessageRouterService(Settings())
    payload = {
        "event": "team_chat.dm_message_posted",
        "event_ts": 1776112556454,
        "payload": {
            "operator": "user@example.com",
            "operator_id": "u1",
            "operator_member_id": "member-1",
            "object": {
                "id": "msg-1",
                "message": "hello from dm",
                "contact_id": "bot-user-id",
                "session_id": "session-1",
            },
        },
    }

    normalized = router.normalize(payload)

    assert normalized is not None
    assert normalized.event_type == "team_chat.dm_message_posted"
    assert normalized.user_text == "hello from dm"
    assert normalized.zoom_user_id == "u1"
    assert normalized.zoom_channel_id == "bot-user-id"
    assert normalized.reply_target.to_jid == "session-1"
    assert normalized.reply_target.channel_id is None


def test_normalize_team_chat_channel_message_posted_payload() -> None:
    router = MessageRouterService(Settings())
    payload = {
        "event": "team_chat.channel_message_posted",
        "event_ts": 1776112556454,
        "payload": {
            "operator": "user@example.com",
            "operator_id": "u1",
            "object": {
                "id": "msg-2",
                "message": "hello channel",
                "channel_id": "channel-1",
                "channel_name": "General",
                "reply_main_message_id": "parent-1",
            },
        },
    }

    normalized = router.normalize(payload)

    assert normalized is not None
    assert normalized.event_type == "team_chat.channel_message_posted"
    assert normalized.user_text == "hello channel"
    assert normalized.zoom_user_id == "u1"
    assert normalized.zoom_channel_id == "channel-1"
    assert normalized.zoom_thread_id == "parent-1"
    assert normalized.reply_target.to_jid == "channel-1"
    assert normalized.reply_target.channel_id == "channel-1"


def test_team_chat_message_from_bot_user_is_ignored() -> None:
    router = MessageRouterService(Settings(ZOOM_BOT_JID="botUser123@xmpp.zoom.us"))
    payload = {
        "event": "team_chat.dm_message_posted",
        "event_ts": 1776112556454,
        "payload": {
            "operator_id": "BotUser123",
            "object": {
                "id": "msg-1",
                "message": "self echo",
                "contact_id": "human-user",
                "session_id": "session-1",
            },
        },
    }

    assert router.normalize(payload) is None
