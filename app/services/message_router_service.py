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
        user_text = self._extract_text(event_payload)
        if not user_text:
            return None

        zoom_user_id = self._pick(event_payload, ["user_id", "userId", "sender_id", "userJid"])
        sender_jid = self._pick(event_payload, ["user_jid", "sender_jid", "userJid"])
        if self._is_bot_message(zoom_user_id, sender_jid):
            return None

        zoom_channel_id = self._pick(
            event_payload,
            [
                "channel_id",
                "channelId",
                "to_channel",
                "toJid",
                "to_jid",
                "channelName",
                "channel_name",
                "channelJid",
                "channel_jid",
            ],
        )
        zoom_thread_id = self._pick(event_payload, ["thread_id", "threadId", "message_id"])
        if not zoom_thread_id:
            zoom_thread_id = str(payload.get("event_ts") or "root")

        if not zoom_user_id or not zoom_channel_id:
            raise MalformedZoomPayloadError("Missing required Zoom identifiers")

        to_jid = self._pick(event_payload, ["to_jid", "toJid", "channel_jid", "chat_jid"])
        if not to_jid:
            to_jid = zoom_channel_id

        locale = self._pick(event_payload, ["locale", "language"])
        event_id = self._pick(event_payload, ["event_id", "id", "message_id", "triggerId", "trigger_id"])

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
                thread_id=zoom_thread_id,
                channel_id=zoom_channel_id,
            ),
            occurred_at=datetime.now(timezone.utc),
            raw_metadata={
                "sender_jid": sender_jid,
                "trigger": self._pick(event_payload, ["cmd", "command"]),
            },
        )

    def _extract_text(self, event_payload: dict) -> str:
        text = self._pick(event_payload, ["cmd", "text", "message", "body"])
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

    def _is_bot_message(self, zoom_user_id: str | None, sender_jid: str | None) -> bool:
        bot_jid = self._settings.zoom_bot_jid
        if not bot_jid:
            return False
        return sender_jid == bot_jid or zoom_user_id == bot_jid
