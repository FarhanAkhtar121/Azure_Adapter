from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.conversation_mapping import ConversationMapping
from app.models.db import Base
from app.repositories.sqlalchemy_conversation_repository import SqlAlchemyConversationRepository


@pytest.mark.asyncio
async def test_create_and_reuse_mapping() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        repo = SqlAlchemyConversationRepository(session)
        mapping = ConversationMapping(
            zoom_user_id="u1",
            zoom_channel_id="c1",
            zoom_thread_id="t1",
            zoom_to_jid="jid1",
            directline_conversation_id="dl1",
            directline_token="token1",
            directline_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            watermark=None,
            locale="en-US",
            last_activity_at=datetime.now(timezone.utc),
            status="active",
        )
        created = await repo.upsert_mapping(mapping)
        loaded = await repo.get_by_zoom_context("u1", "c1", "t1")

        assert loaded is not None
        assert created.id == loaded.id
        assert loaded.directline_conversation_id == "dl1"

        loaded.directline_token = "token2"
        updated = await repo.upsert_mapping(loaded)
        assert updated.directline_token == "token2"

    await engine.dispose()
