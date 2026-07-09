from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field(default="local", alias="APP_ENV")
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")

    database_url: str = Field(
        default="sqlite+aiosqlite:///./zoom_copilot_adapter.db", alias="DATABASE_URL"
    )

    zoom_secret_token: str = "B0ihE2OFS_mcTDVvZEm6cQ"
    zoom_client_id: str = Field(default="", alias="ZOOM_CLIENT_ID")
    zoom_client_secret: str = Field(default="", alias="ZOOM_CLIENT_SECRET")
    zoom_account_id: str = Field(default="", alias="ZOOM_ACCOUNT_ID")
    zoom_s2s_client_id: str = Field(default="", alias="ZOOM_S2S_CLIENT_ID")
    zoom_s2s_client_secret: str = Field(default="", alias="ZOOM_S2S_CLIENT_SECRET")
    zoom_s2s_account_id: str = Field(default="", alias="ZOOM_S2S_ACCOUNT_ID")
    zoom_bot_jid: str = Field(default="", alias="ZOOM_BOT_JID")
    zoom_chatbot_api_base: str = Field(default="https://api.zoom.us", alias="ZOOM_CHATBOT_API_BASE")

    directline_secret: str = Field(default="", alias="DIRECTLINE_SECRET")
    directline_api_base: str = Field(
        default="https://directline.botframework.com/v3/directline", alias="DIRECTLINE_API_BASE"
    )

    poll_interval_seconds: float = Field(default=1.5, alias="POLL_INTERVAL_SECONDS")
    poll_timeout_seconds: int = Field(default=25, alias="POLL_TIMEOUT_SECONDS")
    websocket_idle_window_seconds: float = Field(default=3.0, alias="WEBSOCKET_IDLE_WINDOW_SECONDS")

    conversation_ttl_hours: int = Field(default=24, alias="CONVERSATION_TTL_HOURS")
    cleanup_interval_seconds: int = Field(default=3600, alias="CLEANUP_INTERVAL_SECONDS")

    request_timeout_seconds: float = Field(default=10.0, alias="REQUEST_TIMEOUT_SECONDS")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    debug_transcript_logging: bool = Field(default=False, alias="DEBUG_TRANSCRIPT_LOGGING")

    def validate_required(self) -> None:
        """Raise ValueError listing every required env var that is empty."""
        missing = []
        if not self.zoom_secret_token:
            missing.append("ZOOM_SECRET_TOKEN")
        if not self.zoom_bot_jid:
            missing.append("ZOOM_BOT_JID")
        if not self.zoom_client_id:
            missing.append("ZOOM_CLIENT_ID")
        if not self.zoom_client_secret:
            missing.append("ZOOM_CLIENT_SECRET")
        if not self.directline_secret:
            missing.append("DIRECTLINE_SECRET")
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()