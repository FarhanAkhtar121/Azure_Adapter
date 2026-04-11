from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.core.config import Settings
from app.core.logging import get_logger
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
                    mapping = await self._directline.maybe_refresh_mapping(mapping, self._token_service)

                mapping.locale = normalized.locale
                mapping.zoom_to_jid = normalized.reply_target.to_jid
                mapping.last_zoom_event_id = normalized.event_id
                mapping.last_activity_at = datetime.now(timezone.utc)
                mapping.status = "active"
                mapping = await repo.upsert_mapping(mapping)
                mapping_id = mapping.id

                user_from_id = f"zoom:{normalized.zoom_user_id}"
                await self._directline.send_message(
                    conversation_id=mapping.directline_conversation_id,
                    token=mapping.directline_token,
                    text=normalized.user_text,
                    from_id=user_from_id,
                    locale=normalized.locale,
                    metadata={
                        "zoom_user_id": normalized.zoom_user_id,
                        "zoom_channel_id": normalized.zoom_channel_id,
                        "zoom_thread_id": normalized.zoom_thread_id,
                        "source": "zoom-team-chat",
                    },
                )

                bot_messages, watermark = await self._directline.poll_for_bot_reply(
                    conversation_id=mapping.directline_conversation_id,
                    token=mapping.directline_token,
                    watermark=mapping.watermark,
                    user_from_id=user_from_id,
                )

                access_token = await self._zoom_auth.get_chatbot_access_token()
                for message in bot_messages:
                    await self._zoom_chat.send_text_message(
                        access_token=access_token,
                        to_jid=normalized.reply_target.to_jid,
                        text=message,
                        thread_id=normalized.reply_target.thread_id,
                    )

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

    async def _create_mapping(self, normalized_message) -> ConversationMapping:
        token_response = await self._token_service.get_directline_token()
        conversation_id = await self._directline.ensure_conversation(
            token=token_response.token,
            conversation_id=token_response.conversation_id,
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
