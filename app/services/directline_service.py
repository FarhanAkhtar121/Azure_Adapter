from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from app.core.config import Settings
from app.core.exceptions import DirectLineReceiveTimeoutError, DirectLineSendError
from app.core.logging import get_logger
from app.models.conversation_mapping import ConversationMapping
from app.schemas.directline import DirectLineActivitiesResponse
from app.services.copilot_token_service import CopilotTokenService

logger = get_logger(__name__)


class DirectLineService:
    """Handles Direct Line send/receive flows with polling for bot replies."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self._settings = settings
        self._base = settings.directline_api_base.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=settings.request_timeout_seconds)

    async def maybe_refresh_mapping(
        self,
        mapping: ConversationMapping,
        token_service: CopilotTokenService,
    ) -> ConversationMapping:
        now = datetime.now(timezone.utc)
        expires_at = self._normalize_utc(mapping.directline_token_expires_at)
        mapping.directline_token_expires_at = expires_at
        if expires_at > now:
            return mapping

        token_response = await token_service.get_directline_token()
        mapping.directline_token = token_response.token
        if token_response.conversation_id:
            mapping.directline_conversation_id = token_response.conversation_id
        mapping.directline_token_expires_at = token_service.compute_expiry(token_response.expires_in)
        mapping.status = "active"
        return mapping

    @staticmethod
    def _normalize_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    async def ensure_conversation(self, token: str, conversation_id: str | None) -> str:
        if conversation_id:
            logger.info(
                "Using existing conversation ID",
                extra={"extra": {"conversation_id": conversation_id}},
            )
            return conversation_id
        url = f"{self._base}/conversations"
        logger.info("Creating new Direct Line conversation", extra={"extra": {"url": url}})
        response = await self._client.post(url, headers={"Authorization": f"Bearer {token}"})
        if response.status_code >= 400:
            logger.error(
                "Failed to start Direct Line conversation",
                extra={"extra": {"status": response.status_code, "response_body": response.text}},
            )
            raise DirectLineSendError(f"Failed to start Direct Line conversation: {response.status_code}")
        data = response.json()
        cid = data.get("conversationId")
        if not cid:
            raise DirectLineSendError("Direct Line conversationId missing after start")
        logger.info("New conversation created", extra={"extra": {"conversation_id": cid}})
        return cid

    async def send_message(
        self,
        conversation_id: str,
        token: str,
        text: str,
        from_id: str,
        locale: str | None,
        metadata: dict,
    ) -> str:
        url = f"{self._base}/conversations/{conversation_id}/activities"
        payload: dict = {
            "type": "message",
            "text": text,
            "from": {"id": from_id},
            "channelData": metadata,
        }
        if locale:
            payload["locale"] = locale

        logger.info(
            "Sending Direct Line message",
            extra={"extra": {"url": url, "from_id": from_id, "conversation_id": conversation_id}},
        )

        response = await self._client.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
        if response.status_code >= 400:
            logger.error(
                "Direct Line send failed",
                extra={"extra": {"status": response.status_code, "response_body": response.text}},
            )
            raise DirectLineSendError(f"Direct Line send failed with status {response.status_code}")
        activity_id = response.json().get("id", "")
        return activity_id

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_fixed(0.5),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.HTTPStatusError)),
    )
    async def get_activities(
        self,
        conversation_id: str,
        token: str,
        watermark: str | None,
    ) -> DirectLineActivitiesResponse:
        params: dict[str, str] = {}
        if watermark:
            params["watermark"] = watermark

        url = f"{self._base}/conversations/{conversation_id}/activities"
        response = await self._client.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        )
        if response.status_code >= 500:
            response.raise_for_status()
        if response.status_code >= 400:
            raise DirectLineSendError(f"Direct Line activity receive failed with {response.status_code}")

        return DirectLineActivitiesResponse.model_validate(response.json())

    async def poll_for_bot_reply(
        self,
        conversation_id: str,
        token: str,
        watermark: str | None,
        user_from_id: str,
    ) -> tuple[list[str], str | None]:
        timeout_seconds = self._settings.poll_timeout_seconds
        interval = self._settings.poll_interval_seconds

        start = asyncio.get_event_loop().time()
        seen_ids: set[str] = set()
        messages: list[str] = []
        current_watermark = watermark
        got_first_bot_reply = False

        while (asyncio.get_event_loop().time() - start) < timeout_seconds:
            activities_response = await self.get_activities(
                conversation_id=conversation_id,
                token=token,
                watermark=current_watermark,
            )
            current_watermark = activities_response.watermark or current_watermark

            new_bot_messages = 0
            for activity in activities_response.activities:
                if not activity.id or activity.id in seen_ids:
                    continue
                seen_ids.add(activity.id)

                if activity.type != "message":
                    continue

                sender = activity.from_.id if activity.from_ else ""
                if sender == user_from_id:
                    continue

                if activity.text:
                    messages.append(activity.text)
                    new_bot_messages += 1

            if new_bot_messages > 0:
                got_first_bot_reply = True
            elif got_first_bot_reply:
                break

            await asyncio.sleep(interval)

        if not messages:
            raise DirectLineReceiveTimeoutError("No bot messages received within polling timeout")

        return messages, current_watermark
