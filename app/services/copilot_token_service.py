from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import Settings
from app.core.exceptions import CopilotTokenError
from app.core.logging import get_logger
from app.schemas.directline import DirectLineTokenResponse

logger = get_logger(__name__)


class CopilotTokenService:
    """Retrieves Direct Line tokens from Copilot Studio token endpoint."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self._settings = settings
        self._client = client or httpx.AsyncClient(timeout=settings.request_timeout_seconds)

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.4, min=0.4, max=2),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.HTTPStatusError)),
    )
    async def get_directline_token(self) -> DirectLineTokenResponse:
        endpoint = self._settings.copilot_token_endpoint
        if not endpoint:
            raise CopilotTokenError("COPILOT_TOKEN_ENDPOINT is not configured")

        response = await self._client.get(endpoint)
        if response.status_code >= 500:
            response.raise_for_status()
        if response.status_code >= 400:
            raise CopilotTokenError(f"Copilot token endpoint failed with {response.status_code}")

        try:
            model = DirectLineTokenResponse.model_validate(response.json())
        except Exception as exc:
            raise CopilotTokenError("Invalid token response from Copilot endpoint") from exc

        if not model.token:
            raise CopilotTokenError("Token response missing token value")

        logger.info("Direct Line token acquired", extra={"extra": {"expires_in": model.expires_in}})
        return model

    @staticmethod
    def compute_expiry(expires_in_seconds: int) -> datetime:
        return datetime.now(timezone.utc) + timedelta(seconds=max(expires_in_seconds - 60, 30))
