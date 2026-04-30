from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.api.routes.health import router as health_router
from app.api.routes.zoom_webhook import router as zoom_webhook_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.models import conversation_mapping  # noqa: F401
from app.models.db import Base, build_engine, build_session_factory
from app.services.background_processor import BackgroundProcessor
from app.services.copilot_token_service import CopilotTokenService
from app.services.directline_service import DirectLineService
from app.services.message_router_service import MessageRouterService
from app.services.zoom_auth_service import ZoomAuthService
from app.services.zoom_chat_service import ZoomChatService
from app.services.zoom_signature_service import ZoomSignatureService


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

    @asynccontextmanager
    async def lifespan(app: FastAPI):
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

        yield

        await engine.dispose()

    app = FastAPI(title="Zoom Copilot Adapter", version="1.0.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.signature_service = signature_service
    app.state.background_processor = background_processor

    app.include_router(health_router)
    app.include_router(zoom_webhook_router)
    return app


app = create_app()