from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import RepositoryError
from app.core.logging import get_logger
from app.models.conversation_mapping import ConversationMapping
from app.repositories.conversation_repository import ConversationRepository

logger = get_logger(__name__)


class SqlAlchemyConversationRepository(ConversationRepository):
    """SQLAlchemy async implementation for conversation mappings."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_zoom_context(
        self, zoom_user_id: str, zoom_channel_id: str, zoom_thread_id: str
    ) -> ConversationMapping | None:
        stmt = select(ConversationMapping).where(
            ConversationMapping.zoom_user_id == zoom_user_id,
            ConversationMapping.zoom_channel_id == zoom_channel_id,
            ConversationMapping.zoom_thread_id == zoom_thread_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert_mapping(self, mapping: ConversationMapping) -> ConversationMapping:
        try:
            mapping.updated_at = datetime.now(timezone.utc)
            self._session.add(mapping)
            await self._session.commit()
            await self._session.refresh(mapping)
            return mapping
        except Exception as exc:  # pragma: no cover
            await self._session.rollback()
            raise RepositoryError("Failed to upsert conversation mapping") from exc

    async def update_watermark(self, mapping_id: int, watermark: str | None) -> None:
        stmt = select(ConversationMapping).where(ConversationMapping.id == mapping_id)
        result = await self._session.execute(stmt)
        mapping = result.scalar_one_or_none()
        if not mapping:
            return
        mapping.watermark = watermark
        mapping.last_activity_at = datetime.now(timezone.utc)
        await self.upsert_mapping(mapping)

    async def mark_failed(self, mapping_id: int | None, reason: str) -> None:
        if mapping_id is None:
            logger.warning("mark_failed called with no mapping_id", extra={"extra": {"reason": reason}})
            return
        stmt = select(ConversationMapping).where(ConversationMapping.id == mapping_id)
        result = await self._session.execute(stmt)
        mapping = result.scalar_one_or_none()
        if not mapping:
            return
        mapping.status = "failed"
        metadata = mapping.metadata_json or {}
        metadata["failure_reason"] = reason[:512]
        mapping.metadata_json = metadata
        await self.upsert_mapping(mapping)

    async def delete_expired(self, older_than: datetime) -> int:
        stmt = delete(ConversationMapping).where(ConversationMapping.last_activity_at < older_than)
        result = await self._session.execute(stmt)
        await self._session.commit()
        return int(result.rowcount or 0)

    async def get_recent_active(self, limit: int = 100) -> list[ConversationMapping]:
        stmt = (
            select(ConversationMapping)
            .where(ConversationMapping.status == "active")
            .order_by(ConversationMapping.last_activity_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
