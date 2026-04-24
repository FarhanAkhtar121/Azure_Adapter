from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.exceptions import DirectLineConversationExpiredError
from app.models.conversation_mapping import ConversationMapping
from app.repositories.sqlalchemy_conversation_repository import SqlAlchemyConversationRepository
from app.services.copilot_token_service import CopilotTokenService
from app.services.directline_service import DirectLineService
from app.services.message_router_service import MessageRouterService
from app.services.zoom_auth_service import ZoomAuthService
from app.services.zoom_chat_service import ZoomChatService

logger = get_logger(__name__)


class BackgroundProcessor:
    """Processes Zoom events asynchronously after webhook acknowledgement."""

    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        router_service: MessageRouterService,
        token_service: CopilotTokenService,
        directline_service: DirectLineService,
        zoom_auth_service: ZoomAuthService,
        zoom_chat_service: ZoomChatService,
    ):
        self._settings = settings
        self._session_factory = session_factory
        self._router = router_service
        self._token_service = token_service
        self._directline = directline_service
        self._zoom_auth = zoom_auth_service
        self._zoom_chat = zoom_chat_service

    async def process_event(self, raw_payload: dict, request_id: str) -> None:
        started_at = datetime.now(timezone.utc)
        mapping_id: int | None = None

        try:
            normalized = self._router.normalize(raw_payload)
            if not normalized:
                logger.info(
                    "Ignoring unsupported or empty Zoom event",
                    extra={"extra": {"request_id": request_id, "event": raw_payload.get("event")}},
                )
                return

            async with self._session_factory() as session:
                repo = SqlAlchemyConversationRepository(session)
                mapping = await repo.get_by_zoom_context(
                    normalized.zoom_user_id,
                    normalized.zoom_channel_id,
                    normalized.zoom_thread_id,
                )

                if not mapping:
                    mapping = await self._create_mapping(normalized)
                else:
                    if normalized.event_id and mapping.last_zoom_event_id == normalized.event_id:
                        logger.info(
                            "Ignoring duplicate Zoom event",
                            extra={
                                "extra": {
                                    "request_id": request_id,
                                    "event_type": normalized.event_type,
                                    "event_id": normalized.event_id,
                                }
                            },
                        )
                        return
                    if self._is_recent_outbound_echo(mapping, normalized.user_text):
                        logger.info(
                            "Ignoring recent outbound echo",
                            extra={
                                "extra": {
                                    "request_id": request_id,
                                    "event_type": normalized.event_type,
                                    "event_id": normalized.event_id,
                                    "zoom_thread_id": normalized.zoom_thread_id,
                                }
                            },
                        )
                        return
                    mapping = await self._directline.maybe_refresh_mapping(mapping, self._token_service)

                mapping.locale = normalized.locale
                mapping.zoom_to_jid = normalized.reply_target.to_jid
                mapping.last_zoom_event_id = normalized.event_id
                mapping.last_activity_at = datetime.now(timezone.utc)
                mapping.status = "active"
                mapping = await repo.upsert_mapping(mapping)
                mapping_id = mapping.id

                # Emulate Action.Submit: if the previous bot reply had an adaptive card
                # with input fields, map the user's text to those field IDs so Copilot
                # Studio receives a structured form value rather than plain text.
                meta = dict(mapping.metadata_json or {})
                pending_card_inputs: list[dict] = meta.pop("pending_card_inputs", None) or []
                submit_value: dict | None = None
                outbound_text = normalized.user_text
                if pending_card_inputs:
                    action_data: dict = {}
                    for inp in pending_card_inputs:
                        raw_action_data = inp.get("action_data")
                        if isinstance(raw_action_data, dict):
                            action_data.update(raw_action_data)

                    submit_value = dict(action_data)

                    # Preserve defaults from card values when present.
                    for inp in pending_card_inputs:
                        field_id = inp.get("id")
                        default_value = inp.get("value")
                        if field_id and default_value is not None and str(default_value).strip() != "":
                            submit_value[field_id] = default_value

                    field_ids = [inp.get("id") for inp in pending_card_inputs if inp.get("id")]
                    parsed_updates = self._parse_structured_card_reply(normalized.user_text, pending_card_inputs)
                    if parsed_updates:
                        submit_value.update(parsed_updates)
                    elif len(field_ids) == 1:
                        submit_value[field_ids[0]] = normalized.user_text

                    # Submit-style activity: preserve value payload; text is optional.
                    outbound_text = ""
                    mapping.metadata_json = meta
                    logger.info(
                        "Emulating card Action.Submit for user reply",
                        extra={
                            "extra": {
                                "input_ids": [inp.get("id") for inp in pending_card_inputs if inp.get("id")],
                                "action_data_keys": sorted(action_data.keys()),
                                "parsed_update_keys": sorted(parsed_updates.keys()) if parsed_updates else [],
                                "value_keys": sorted(submit_value.keys()),
                            }
                        },
                    )

                user_from_id = f"zoom:{normalized.zoom_user_id}"
                _send_metadata = {
                    "zoom_user_id": normalized.zoom_user_id,
                    "zoom_channel_id": normalized.zoom_channel_id,
                    "zoom_thread_id": normalized.zoom_thread_id,
                    "source": "zoom-team-chat",
                }
                try:
                    await self._directline.send_message(
                        conversation_id=mapping.directline_conversation_id,
                        token=mapping.directline_token,
                        text=outbound_text,
                        from_id=user_from_id,
                        locale=normalized.locale,
                        metadata=_send_metadata,
                        value=submit_value,
                    )
                except DirectLineConversationExpiredError:
                    logger.warning(
                        "Direct Line conversation expired — resetting and retrying",
                        extra={
                            "extra": {
                                "request_id": request_id,
                                "stale_conversation_id": mapping.directline_conversation_id,
                            }
                        },
                    )
                    mapping = await self._reset_conversation(mapping)
                    mapping = await repo.upsert_mapping(mapping)
                    mapping_id = mapping.id
                    await self._directline.send_message(
                        conversation_id=mapping.directline_conversation_id,
                        token=mapping.directline_token,
                        text=outbound_text,
                        from_id=user_from_id,
                        locale=normalized.locale,
                        metadata=_send_metadata,
                        value=submit_value,
                    )

                bot_messages, watermark, detected_inputs = await self._directline.poll_for_bot_reply(
                    conversation_id=mapping.directline_conversation_id,
                    token=mapping.directline_token,
                    watermark=mapping.watermark,
                    user_from_id=user_from_id,
                )

                if detected_inputs:
                    meta = dict(mapping.metadata_json or {})
                    meta["pending_card_inputs"] = detected_inputs
                    mapping.metadata_json = meta
                    logger.info(
                        "Adaptive card inputs detected — will emulate Action.Submit on next reply",
                        extra={
                            "extra": {
                                "input_ids": [inp.get("id") for inp in detected_inputs if inp.get("id")],
                            }
                        },
                    )

                access_token = await self._zoom_auth.get_chatbot_access_token()
                for message in bot_messages:
                    await self._zoom_chat.send_text_message(
                        access_token=access_token,
                        to_jid=normalized.reply_target.to_jid,
                        text=message,
                        user_jid=normalized.reply_target.user_jid or normalized.zoom_user_id,
                        account_id=normalized.reply_target.account_id,
                    )
                    self._record_outbound_message(mapping, message)

                mapping.watermark = watermark
                mapping.last_activity_at = datetime.now(timezone.utc)
                await repo.upsert_mapping(mapping)

                duration_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
                logger.info(
                    "Background event processed",
                    extra={
                        "extra": {
                            "request_id": request_id,
                            "event_type": normalized.event_type,
                            "zoom_user_id": normalized.zoom_user_id,
                            "zoom_channel_id": normalized.zoom_channel_id,
                            "zoom_thread_id": normalized.zoom_thread_id,
                            "directline_conversation_id": mapping.directline_conversation_id,
                            "duration_ms": duration_ms,
                            "status": "success",
                        }
                    },
                )
        except Exception as exc:
            logger.exception(
                "Background processing failed",
                extra={"extra": {"request_id": request_id, "status": "failed", "error": str(exc)}},
            )
            if mapping_id is not None:
                async with self._session_factory() as session:
                    repo = SqlAlchemyConversationRepository(session)
                    await repo.mark_failed(mapping_id, str(exc))

    async def _reset_conversation(self, mapping: ConversationMapping) -> ConversationMapping:
        """Obtain a fresh Direct Line token + conversation and update the mapping in place."""
        token_response = await self._token_service.get_directline_token()
        logger.info(
            "Fresh token acquired for reset conversation",
            extra={
                "extra": {
                    "conversation_id_from_token": token_response.conversation_id,
                    "expires_in": token_response.expires_in,
                }
            },
        )
        conversation_id = await self._directline.ensure_conversation(
            token=token_response.token,
            conversation_id=None,
        )
        logger.info(
            "Replacement conversation created",
            extra={"extra": {"conversation_id": conversation_id}},
        )
        mapping.directline_conversation_id = conversation_id
        mapping.directline_token = token_response.token
        mapping.directline_token_expires_at = self._token_service.compute_expiry(token_response.expires_in)
        mapping.watermark = None
        mapping.status = "active"
        return mapping

    async def _create_mapping(self, normalized_message) -> ConversationMapping:
        token_response = await self._token_service.get_directline_token()
        logger.info(
            "Token response received",
            extra={
                "extra": {
                    "conversation_id_from_token": token_response.conversation_id,
                    "expires_in": token_response.expires_in,
                }
            },
        )
        conversation_id = await self._directline.ensure_conversation(
            token=token_response.token,
            conversation_id=None,
        )
        logger.info(
            "Conversation ensured",
            extra={"extra": {"conversation_id": conversation_id}},
        )
        return ConversationMapping(
            zoom_user_id=normalized_message.zoom_user_id,
            zoom_channel_id=normalized_message.zoom_channel_id,
            zoom_thread_id=normalized_message.zoom_thread_id,
            zoom_to_jid=normalized_message.reply_target.to_jid,
            directline_conversation_id=conversation_id,
            directline_token=token_response.token,
            directline_token_expires_at=self._token_service.compute_expiry(token_response.expires_in),
            watermark=None,
            locale=normalized_message.locale,
            last_activity_at=datetime.now(timezone.utc),
            status="active",
            metadata_json={"source": "zoom-team-chat"},
            last_zoom_event_id=normalized_message.event_id,
        )

    @staticmethod
    def _is_recent_outbound_echo(mapping: ConversationMapping, inbound_text: str) -> bool:
        metadata = mapping.metadata_json or {}
        last_outbound_text = metadata.get("last_outbound_text")
        last_outbound_at = metadata.get("last_outbound_at")
        if not last_outbound_text or not last_outbound_at:
            return False

        try:
            outbound_at = datetime.fromisoformat(last_outbound_at)
        except ValueError:
            return False

        if outbound_at.tzinfo is None:
            outbound_at = outbound_at.replace(tzinfo=timezone.utc)
        else:
            outbound_at = outbound_at.astimezone(timezone.utc)

        if datetime.now(timezone.utc) - outbound_at > timedelta(seconds=30):
            return False

        return inbound_text.strip() == str(last_outbound_text).strip()

    @staticmethod
    def _record_outbound_message(mapping: ConversationMapping, message: str) -> None:
        metadata = dict(mapping.metadata_json or {})
        metadata["last_outbound_text"] = message
        metadata["last_outbound_at"] = datetime.now(timezone.utc).isoformat()
        mapping.metadata_json = metadata

    @staticmethod
    def _parse_structured_card_reply(user_text: str, inputs: list[dict]) -> dict[str, str]:
        """Parse a user reply into per-field values for multi-input adaptive cards.

        Supports:
        - JSON object: {"field_id": "value"}
        - Key-value lines: field: value
        - Keys matching input id or label (case-insensitive)
        """
        text = (user_text or "").strip()
        if not text:
            return {}

        alias_to_id: dict[str, str] = {}
        for inp in inputs:
            field_id = inp.get("id")
            if not field_id:
                continue
            alias_to_id[field_id.strip().lower()] = field_id
            label = inp.get("label")
            if isinstance(label, str) and label.strip():
                alias_to_id[label.strip().lower()] = field_id

        def _normalize_key(k: str) -> str:
            return " ".join(k.replace("_", " ").split()).strip().lower()

        normalized_aliases = {_normalize_key(k): v for k, v in alias_to_id.items()}

        updates: dict[str, str] = {}

        # JSON object payload
        if text.startswith("{") and text.endswith("}"):
            try:
                obj = json.loads(text)
            except json.JSONDecodeError:
                obj = None
            if isinstance(obj, dict):
                for raw_key, raw_value in obj.items():
                    if not isinstance(raw_key, str):
                        continue
                    mapped = alias_to_id.get(raw_key.strip().lower()) or normalized_aliases.get(
                        _normalize_key(raw_key)
                    )
                    if mapped is not None and raw_value is not None:
                        updates[mapped] = str(raw_value)
                if updates:
                    return updates

        # Key-value lines
        for line in text.splitlines():
            if ":" not in line:
                continue
            raw_key, raw_value = line.split(":", 1)
            key = raw_key.strip()
            value = raw_value.strip()
            if not key or not value:
                continue
            mapped = alias_to_id.get(key.lower()) or normalized_aliases.get(_normalize_key(key))
            if mapped:
                updates[mapped] = value

        return updates