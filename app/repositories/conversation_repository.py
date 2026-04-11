from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from app.models.conversation_mapping import ConversationMapping


class ConversationRepository(ABC):
    """Repository abstraction for conversation mapping persistence."""

    @abstractmethod
    async def get_by_zoom_context(
        self, zoom_user_id: str, zoom_channel_id: str, zoom_thread_id: str
    ) -> ConversationMapping | None:
        raise NotImplementedError

    @abstractmethod
    async def upsert_mapping(self, mapping: ConversationMapping) -> ConversationMapping:
        raise NotImplementedError

    @abstractmethod
    async def update_watermark(self, mapping_id: int, watermark: str | None) -> None:
        raise NotImplementedError

    @abstractmethod
    async def mark_failed(self, mapping_id: int | None, reason: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def delete_expired(self, older_than: datetime) -> int:
        raise NotImplementedError

    @abstractmethod
    async def get_recent_active(self, limit: int = 100) -> list[ConversationMapping]:
        raise NotImplementedError
