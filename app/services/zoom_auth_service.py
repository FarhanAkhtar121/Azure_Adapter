from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import Settings
from app.core.exceptions import ZoomReplyError
from app.core.logging import get_logger

logger = get_logger(__name__)


class ZoomAuthService:
    """Retrieves and caches Zoom chatbot OAuth tokens for outbound chatbot replies."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self._settings = settings
        self._client = client or httpx.AsyncClient(timeout=settings.request_timeout_seconds)
        self._cached_token: str | None = None
        self._expires_at: datetime | None = None
        self._lock = asyncio.Lock()

    async def get_chatbot_access_token(self) -> str:
        async with self._lock:
            if self._cached_token and self._expires_at and datetime.now(timezone.utc) < self._expires_at:
                return self._cached_token

            token, expires_in = await self._fetch_token()
            self._cached_token = token
            self._expires_at = datetime.now(timezone.utc) + timedelta(seconds=max(expires_in - 60, 60))
            return token

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=2),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.HTTPStatusError)),
    )
    async def _fetch_token(self) -> tuple[str, int]:
        client_id, client_secret = self._resolve_chatbot_credentials()

        url = "https://zoom.us/oauth/token"
        params = {"grant_type": "client_credentials"}

        logger.info(
            "Requesting Zoom chatbot access token",
            extra={
                "extra": {
                    "url": url,
                    "grant_type": "client_credentials",
                    "credential_source": "chatbot",
                }
            },
        )

        response = await self._client.post(
            url,
            params=params,
            auth=httpx.BasicAuth(client_id, client_secret),
        )

        if response.status_code >= 500:
            response.raise_for_status()
        if response.status_code >= 400:
            logger.error(
                "Zoom chatbot auth failed",
                extra={"extra": {"status": response.status_code, "response_body": response.text}},
            )
            raise ZoomReplyError(f"Zoom chatbot auth failed with status {response.status_code}")

        data = response.json()
        token = data.get("access_token")
        expires_in = int(data.get("expires_in", 3600))
        if not token:
            raise ZoomReplyError("Zoom chatbot auth response missing access_token")

        logger.info("Zoom chatbot access token refreshed", extra={"extra": {"expires_in": expires_in}})
        return token, expires_in

    def _resolve_chatbot_credentials(self) -> tuple[str, str]:
        if not self._settings.zoom_client_id or not self._settings.zoom_client_secret:
            raise ZoomReplyError("Zoom chatbot client credentials are not configured")

        return self._settings.zoom_client_id, self._settings.zoom_client_secret