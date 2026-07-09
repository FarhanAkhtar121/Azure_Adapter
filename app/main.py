from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI

from app.api.routes.health import router as health_router
from app.api.routes.zoom_webhook import router as zoom_webhook_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.models import conversation_mapping  # noqa: F401
from app.models.db import Base, build_engine, build_session_factory
from app.repositories.sqlalchemy_conversation_repository import SqlAlchemyConversationRepository
from app.services.background_processor import BackgroundProcessor
from app.services.copilot_token_service import CopilotTokenService
from app.services.directline_service import DirectLineService
from app.services.message_router_service import MessageRouterService
from app.services.zoom_auth_service import ZoomAuthService
from app.services.zoom_chat_service import ZoomChatService
from app.services.zoom_signature_service import ZoomSignatureService

logger = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    engine = build_engine(settings)
    session_factory = build_session_factory(engine)

    signature_service = ZoomSignatureService(settings)
    router_service = MessageRouterService(settings)
    token_service = CopilotTokenService(settings)
    directline_service = DirectLineService(settings)
    zoom_auth_service = ZoomAuthService(settings)
    zoom_chat_service = ZoomChatService(settings)

    background_processor = BackgroundProcessor(
        settings=settings,
        session_factory=session_factory,
        router_service=router_service,
        token_service=token_service,
        directline_service=directline_service,
        zoom_auth_service=zoom_auth_service,
        zoom_chat_service=zoom_chat_service,
    )

    async def _cleanup_loop(interval_seconds: int, ttl_hours: int) -> None:
        """Periodically delete conversation mappings older than ttl_hours."""
        while True:
            await asyncio.sleep(interval_seconds)
            cutoff = datetime.now(timezone.utc) - timedelta(hours=ttl_hours)
            try:
                async with session_factory() as session:
                    repo = SqlAlchemyConversationRepository(session)
                    deleted = await repo.delete_expired(cutoff)
                if deleted:
                    logger.info(
                        "Expired conversation mappings deleted",
                        extra={"extra": {"count": deleted, "cutoff": cutoff.isoformat()}},
                    )
            except Exception as exc:
                logger.exception(
                    "Cleanup loop error",
                    extra={"extra": {"error": str(exc)}},
                )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Validate required env vars before accepting traffic.
        settings.validate_required()
        logger.info("Configuration validated — all required env vars present")

        db_url = settings.database_url

        # Ensure SQLite directory exists before SQLAlchemy tries to connect.
        # This is especially important on Azure App Service when using a path
        # like /home/site/wwwroot/data/zoom_copilot_adapter.db.
        if db_url.startswith("sqlite"):
            sqlite_path = db_url.replace("sqlite+aiosqlite:///", "", 1)
            db_file = Path(sqlite_path)
            db_file.parent.mkdir(parents=True, exist_ok=True)

        # Create tables automatically for local/dev, and also for SQLite-based deployments
        # such as temporary Azure App Service testing.
        if db_url.startswith("sqlite") or settings.app_env.lower() in {"local", "dev", "development"}:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        cleanup_task = asyncio.create_task(
            _cleanup_loop(settings.cleanup_interval_seconds, settings.conversation_ttl_hours)
        )

        yield

        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass

        await engine.dispose()

    app = FastAPI(title="Zoom Copilot Adapter", version="1.0.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.signature_service = signature_service
    app.state.background_processor = background_processor

    app.include_router(health_router)
    app.include_router(zoom_webhook_router)
    return app


app = create_app()