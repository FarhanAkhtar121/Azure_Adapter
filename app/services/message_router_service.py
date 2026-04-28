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
        "team_chat.plain_text_input",
        "interactive_message_actions",
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

        input_action_id: str | None = None
        input_value: str | None = None
        submit_action_value: str | None = None

        if event_type == "team_chat.plain_text_input":
            input_action_id, input_value = self._extract_plain_text_input(event_payload, object_payload)
            user_text = input_value or ""
        elif event_type == "interactive_message_actions":
            submit_action_value = self._extract_interactive_action_value(event_payload, object_payload)
            user_text = submit_action_value or self._extract_text(event_payload, object_payload)
        else:
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
        if event_type in {"team_chat.plain_text_input", "interactive_message_actions"}:
            # Keep interactive card edit and submit events in the same stable context.
            zoom_thread_id = "root"
        else:
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
                "input_action_id": input_action_id,
                "input_value": input_value,
                "submit_action_value": submit_action_value,
            },
        )

    @staticmethod
    def _extract_plain_text_input(event_payload: dict, object_payload: dict) -> tuple[str | None, str | None]:
        """Extract action_id + value from a team_chat.plain_text_input payload."""
        candidates = [object_payload, event_payload]

        for source in candidates:
            input_obj = source.get("input") if isinstance(source, dict) else None
            if isinstance(input_obj, dict):
                action_id = input_obj.get("action_id") or input_obj.get("actionId")
                value = (
                    input_obj.get("value")
                    or input_obj.get("input_value")
                    or input_obj.get("inputValue")
                )
                if isinstance(action_id, str) and isinstance(value, str) and value.strip():
                    return action_id.strip(), value.strip()

            if isinstance(source, dict):
                action_id = source.get("action_id") or source.get("actionId")
                value = source.get("value") or source.get("input_value") or source.get("inputValue")
                if isinstance(action_id, str) and isinstance(value, str) and value.strip():
                    return action_id.strip(), value.strip()

        # Fallback recursive scan for {"action_id": ..., "value": ...}
        def _scan(node: object) -> tuple[str | None, str | None]:
            if isinstance(node, dict):
                action_id = node.get("action_id") or node.get("actionId")
                value = node.get("value") or node.get("input_value") or node.get("inputValue")
                if isinstance(action_id, str) and isinstance(value, str) and value.strip():
                    return action_id.strip(), value.strip()
                for v in node.values():
                    found_action, found_value = _scan(v)
                    if found_action and found_value:
                        return found_action, found_value
            elif isinstance(node, list):
                for item in node:
                    found_action, found_value = _scan(item)
                    if found_action and found_value:
                        return found_action, found_value
            return None, None

        return _scan(object_payload) if object_payload else _scan(event_payload)

    @staticmethod
    def _extract_interactive_action_value(event_payload: dict, object_payload: dict) -> str | None:
        """Extract button action value from interactive_message_actions payload."""
        for source in (object_payload, event_payload):
            actions = source.get("actions") if isinstance(source, dict) else None
            if isinstance(actions, list):
                for action in actions:
                    if isinstance(action, dict):
                        value = action.get("value")
                        if isinstance(value, str) and value.strip():
                            return value.strip()

        def _scan(node: object) -> str | None:
            if isinstance(node, dict):
                value = node.get("value")
                if isinstance(value, str) and value.strip():
                    return value.strip()
                for child in node.values():
                    found = _scan(child)
                    if found:
                        return found
            elif isinstance(node, list):
                for item in node:
                    found = _scan(item)
                    if found:
                        return found
            return None

        return _scan(object_payload) or _scan(event_payload)

    def _extract_text(self, event_payload: dict, object_payload: dict) -> str:
        text = self._pick_from_sources(
            [object_payload, event_payload],
            ["cmd", "text", "message", "body", "value", "input", "input_value"],
        )
        if isinstance(text, dict):
            text = text.get("text", "")
        if isinstance(text, str) and text.strip():
            return text

        # Interactive events can put values under nested objects/lists.
        def _extract_nested_string(node: object) -> str | None:
            if isinstance(node, str):
                return node.strip() or None
            if isinstance(node, dict):
                for key in (
                    "value",
                    "cmd",
                    "text",
                    "message",
                    "input_value",
                    "input",
                    "content",
                    "action",
                    "actions",
                    "data",
                    "body",
                ):
                    if key in node:
                        found = _extract_nested_string(node.get(key))
                        if found:
                            return found
                for value in node.values():
                    found = _extract_nested_string(value)
                    if found:
                        return found
                return None
            if isinstance(node, list):
                for item in node:
                    found = _extract_nested_string(item)
                    if found:
                        return found
            return None

        nested = _extract_nested_string(object_payload) or _extract_nested_string(event_payload)
        return nested or ""

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