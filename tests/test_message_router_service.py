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


def test_normalize_uses_stable_root_thread_when_thread_missing() -> None:
    router = MessageRouterService(Settings(ZOOM_BOT_JID="bot@xmpp.zoom.us"))

    payload_first = {
        "event": "bot_notification",
        "event_ts": 1710000000,
        "payload": {
            "cmd": "create ticket",
            "user_id": "u1",
            "channel_id": "c1",
            "to_jid": "tojid",
        },
    }
    payload_second = {
        "event": "bot_notification",
        "event_ts": 1710001234,
        "payload": {
            "cmd": "yes",
            "user_id": "u1",
            "channel_id": "c1",
            "to_jid": "tojid",
        },
    }

    normalized_first = router.normalize(payload_first)
    normalized_second = router.normalize(payload_second)

    assert normalized_first is not None
    assert normalized_second is not None
    assert normalized_first.zoom_thread_id == "root"
    assert normalized_second.zoom_thread_id == "root"
    assert normalized_first.zoom_thread_id == normalized_second.zoom_thread_id


def test_normalize_team_chat_plain_text_input_event() -> None:
    router = MessageRouterService(Settings(ZOOM_BOT_JID="bot@xmpp.zoom.us"))
    payload = {
        "event": "team_chat.plain_text_input",
        "payload": {
            "user_id": "u1",
            "to_jid": "tojid",
            "channel_id": "c1",
            "object": {
                "input": {
                    "action_id": "userClarificationResponse",
                    "value": "I am using windows 11 in UMG network",
                }
            },
        },
    }

    normalized = router.normalize(payload)

    assert normalized is not None
    assert normalized.event_type == "team_chat.plain_text_input"
    assert normalized.user_text == "I am using windows 11 in UMG network"
    assert normalized.zoom_user_id == "u1"
    assert normalized.zoom_channel_id == "c1"
    assert normalized.zoom_thread_id == "root"
    assert normalized.raw_metadata["input_action_id"] == "userClarificationResponse"
    assert normalized.raw_metadata["input_value"] == "I am using windows 11 in UMG network"


def test_normalize_interactive_message_actions_event() -> None:
    router = MessageRouterService(Settings(ZOOM_BOT_JID="bot@xmpp.zoom.us"))
    payload = {
        "event": "interactive_message_actions",
        "payload": {
            "user_id": "u1",
            "to_jid": "tojid",
            "channel_id": "c1",
            "object": {
                "actions": [
                    {
                        "text": "Submit details",
                        "value": '{"zoom_action":"submit_card","input_values":{"userClarificationResponse":"I am using windows 11"},"action_data":{"ticketStage":"clarification"}}',
                    }
                ]
            },
        },
    }

    normalized = router.normalize(payload)

    assert normalized is not None
    assert normalized.event_type == "interactive_message_actions"
    assert normalized.user_text.startswith('{"zoom_action":"submit_card"')
    assert normalized.zoom_user_id == "u1"
    assert normalized.zoom_channel_id == "c1"
    assert normalized.zoom_thread_id == "root"
    assert normalized.raw_metadata["submit_action_value"] == normalized.user_text
    assert normalized.raw_metadata["submit_input_values"] == {}


def test_normalize_interactive_message_actions_extracts_input_values() -> None:
    router = MessageRouterService(Settings(ZOOM_BOT_JID="bot@xmpp.zoom.us"))
    payload = {
        "event": "interactive_message_actions",
        "payload": {
            "user_id": "u1",
            "to_jid": "tojid",
            "channel_id": "c1",
            "object": {
                "actions": [
                    {
                        "text": "Confirm and Create Ticket",
                        "value": '{"zoom_action":"submit_card","action_data":{"ticketStage":"review"}}',
                    }
                ],
                "inputs": [
                    {
                        "action_id": "confirmed_short_description",
                        "value": "Unable to map HR shared drive on Windows 11 (UMG network) using vpn",
                    },
                    {
                        "action_id": "confirmed_Category",
                        "value": "Network",
                    },
                ],
            },
        },
    }

    normalized = router.normalize(payload)

    assert normalized is not None
    assert normalized.event_type == "interactive_message_actions"
    assert normalized.raw_metadata["submit_input_values"] == {
        "confirmed_short_description": "Unable to map HR shared drive on Windows 11 (UMG network) using vpn",
        "confirmed_Category": "Network",
    }


def test_normalize_plain_text_input_ignores_label_text_without_value() -> None:
    router = MessageRouterService(Settings(ZOOM_BOT_JID="bot@xmpp.zoom.us"))
    payload = {
        "event": "team_chat.plain_text_input",
        "payload": {
            "user_id": "u1",
            "to_jid": "tojid",
            "channel_id": "c1",
            "object": {
                "input": {
                    "action_id": "userClarificationResponse",
                    "text": "Your answers",
                }
            },
        },
    }

    # No actual value/input_value present; should be ignored.
    assert router.normalize(payload) is None


def test_normalize_plain_text_input_prefers_input_value_over_value() -> None:
    router = MessageRouterService(Settings(ZOOM_BOT_JID="bot@xmpp.zoom.us"))
    payload = {
        "event": "team_chat.plain_text_input",
        "payload": {
            "user_id": "u1",
            "to_jid": "tojid",
            "channel_id": "c1",
            "object": {
                "snapshot": {
                    "action_id": "confirmed_short_description",
                    "value": "Unable to map HR shared drive on Windows 11 (UMG network)",
                },
                "input": {
                    "action_id": "confirmed_short_description",
                    "input_value": "Unable to map HR shared drive on Windows 11 (UMG network) using wifi",
                },
            },
        },
    }

    normalized = router.normalize(payload)

    assert normalized is not None
    assert normalized.user_text.endswith("using wifi")
    assert normalized.raw_metadata["input_value"].endswith("using wifi")


def test_normalize_interactive_message_actions_prefers_input_value_over_value() -> None:
    router = MessageRouterService(Settings(ZOOM_BOT_JID="bot@xmpp.zoom.us"))
    payload = {
        "event": "interactive_message_actions",
        "payload": {
            "user_id": "u1",
            "to_jid": "tojid",
            "channel_id": "c1",
            "object": {
                "actions": [
                    {
                        "text": "Confirm and Create Ticket",
                        "value": '{"zoom_action":"submit_card","action_data":{"ticketStage":"review"}}',
                    }
                ],
                "snapshot_inputs": [
                    {
                        "action_id": "confirmed_short_description",
                        "value": "Unable to map HR shared drive on Windows 11 (UMG network)",
                    }
                ],
                "live_inputs": [
                    {
                        "action_id": "confirmed_short_description",
                        "input_value": "Unable to map HR shared drive on Windows 11 (UMG network) using wifi",
                    }
                ],
            },
        },
    }

    normalized = router.normalize(payload)

    assert normalized is not None
    assert normalized.raw_metadata["submit_input_values"]["confirmed_short_description"].endswith("using wifi")
