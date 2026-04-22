from __future__ import annotations

from datetime import datetime, timezone

from app.core.config import Settings
from app.core.exceptions import MalformedZoomPayloadError
from app.schemas.common import NormalizedInboundMessage, ReplyTarget


class MessageRouterService:
    """Normalizes heterogeneous Zoom webhook payloads into a stable internal model."""

    SUPPORTED_EVENT_TYPES = {
        "bot_notification",
        "slash_command",
        "im.chat.message",
        "message",
        "team_chat.dm_message_posted",
        "team_chat.channel_message_posted",
        "team_chat.app_mention",
    }

    def __init__(self, settings: Settings):
        self._settings = settings

    def normalize(self, payload: dict) -> NormalizedInboundMessage | None:
        event_type = payload.get("event")
        if not event_type:
            raise MalformedZoomPayloadError("Missing event type")

        if event_type not in self.SUPPORTED_EVENT_TYPES:
            return None

        event_payload = payload.get("payload") or {}
        object_payload = event_payload.get("object") if isinstance(event_payload.get("object"), dict) else {}

        user_text = self._extract_text(event_payload, object_payload)
        if not user_text:
            return None

        zoom_user_id = self._pick_from_sources(
            [event_payload, object_payload],
            ["operator_id", "user_id", "userId", "sender_id", "operator_member_id", "userJid"],
        )

        sender_jid = self._pick_from_sources(
            [event_payload, object_payload],
            ["userJid", "user_jid", "sender_jid"],
        )

        if self._is_bot_message(zoom_user_id, sender_jid):
            return None

        explicit_channel_id = self._pick_from_sources(
            [object_payload, event_payload],
            ["channel_id", "channelId", "to_channel"],
        )

        zoom_channel_id = explicit_channel_id or self._pick_from_sources(
            [object_payload, event_payload],
            [
                "contact_id",
                "contact_member_id",
                "contact_email",
                "toJid",
                "to_jid",
                "channelName",
                "channel_name",
                "channelJid",
                "channel_jid",
                "session_id",
            ],
        )

        # Only treat true thread identifiers as thread context.
        # Do not use message_id/id/event_ts, as they can change per message and
        # unintentionally split one user conversation into multiple Direct Line sessions.
        zoom_thread_id = self._pick_from_sources(
            [object_payload, event_payload],
            ["reply_main_message_id", "thread_id", "threadId"],
        )
        if not zoom_thread_id:
            # Stable fallback for non-threaded chat traffic.
            # Since zoom_channel_id is already part of the repository key, using
            # a constant root thread keeps continuity within the same chat context.
            zoom_thread_id = "root"

        if not zoom_user_id or not zoom_channel_id:
            raise MalformedZoomPayloadError("Missing required Zoom identifiers")

        to_jid = self._pick_from_sources(
            [object_payload, event_payload],
            ["toJid", "to_jid", "channel_jid", "chat_jid", "session_id", "contact_id", "contact_member_id"],
        )
        if not to_jid:
            to_jid = zoom_channel_id

        user_jid = sender_jid or self._pick_from_sources(
            [event_payload, object_payload],
            ["userJid", "user_jid"],
        )

        account_id = self._pick_from_sources(
            [event_payload, object_payload],
            ["accountId", "account_id"],
        )

        locale = self._pick_from_sources([event_payload, object_payload], ["locale", "language"])
        event_id = self._pick_from_sources(
            [object_payload, event_payload],
            ["event_id", "id", "message_id", "triggerId", "trigger_id"],
        )

        return NormalizedInboundMessage(
            event_type=event_type,
            event_id=event_id,
            user_text=user_text.strip(),
            zoom_user_id=zoom_user_id,
            zoom_channel_id=zoom_channel_id,
            zoom_thread_id=zoom_thread_id,
            locale=locale,
            reply_target=ReplyTarget(
                to_jid=to_jid,
                user_jid=user_jid,
                account_id=account_id,
                thread_id=zoom_thread_id,
                channel_id=explicit_channel_id,
            ),
            occurred_at=datetime.now(timezone.utc),
            raw_metadata={
                "sender_jid": sender_jid,
                "account_id": account_id,
                "trigger": self._pick_from_sources([event_payload, object_payload], ["cmd", "command"]),
                "operator": self._pick(event_payload, ["operator"]),
            },
        )

    def _extract_text(self, event_payload: dict, object_payload: dict) -> str:
        text = self._pick_from_sources([object_payload, event_payload], ["cmd", "text", "message", "body"])
        if isinstance(text, dict):
            text = text.get("text", "")
        return text or ""

    @staticmethod
    def _pick(payload: dict, keys: list[str]) -> str | None:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @classmethod
    def _pick_from_sources(cls, payloads: list[dict], keys: list[str]) -> str | None:
        for source in payloads:
            value = cls._pick(source, keys)
            if value:
                return value
        return None

    def _is_bot_message(self, zoom_user_id: str | None, sender_jid: str | None) -> bool:
        bot_jid = self._settings.zoom_bot_jid
        if not bot_jid:
            return False

        bot_jid_normalized = bot_jid.strip().lower()
        bot_user_id = bot_jid_normalized.split("@", 1)[0]
        sender_jid_normalized = sender_jid.strip().lower() if sender_jid else None
        zoom_user_id_normalized = zoom_user_id.strip().lower() if zoom_user_id else None

        return sender_jid_normalized == bot_jid_normalized or zoom_user_id_normalized == bot_user_id