from datetime import datetime, timedelta, timezone

from app.models.conversation_mapping import ConversationMapping
from app.services.background_processor import BackgroundProcessor


def make_mapping(metadata_json: dict | None = None) -> ConversationMapping:
    return ConversationMapping(
        zoom_user_id="u",
        zoom_channel_id="c",
        zoom_thread_id="t",
        zoom_to_jid="jid",
        directline_conversation_id="conv",
        directline_token="token",
        directline_token_expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        last_activity_at=datetime.now(timezone.utc),
        status="active",
        metadata_json=metadata_json,
    )


def test_recent_outbound_echo_is_detected() -> None:
    mapping = make_mapping(
        {
            "last_outbound_text": "Hello from bot",
            "last_outbound_at": datetime.now(timezone.utc).isoformat(),
        }
    )

    assert BackgroundProcessor._is_recent_outbound_echo(mapping, "Hello from bot") is True


def test_stale_or_different_outbound_message_is_not_detected_as_echo() -> None:
    mapping = make_mapping(
        {
            "last_outbound_text": "Hello from bot",
            "last_outbound_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        }
    )

    assert BackgroundProcessor._is_recent_outbound_echo(mapping, "Hello from bot") is False
    assert BackgroundProcessor._is_recent_outbound_echo(mapping, "Different text") is False


def test_record_outbound_message_updates_mapping_metadata() -> None:
    mapping = make_mapping({"source": "zoom-team-chat"})

    BackgroundProcessor._record_outbound_message(mapping, "Bot says hi")

    assert mapping.metadata_json is not None
    assert mapping.metadata_json["last_outbound_text"] == "Bot says hi"
    assert "last_outbound_at" in mapping.metadata_json