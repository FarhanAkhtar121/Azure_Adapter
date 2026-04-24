from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from app.core.config import Settings
from app.core.exceptions import DirectLineConversationExpiredError, DirectLineReceiveTimeoutError, DirectLineSendError
from app.core.logging import get_logger
from app.models.conversation_mapping import ConversationMapping
from app.schemas.directline import DirectLineActivitiesResponse, DirectLineAttachment
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
        value: dict | None = None,
    ) -> str:
        url = f"{self._base}/conversations/{conversation_id}/activities"
        payload: dict = {
            "type": "message",
            "text": text,
            "from": {"id": from_id},
            "channelData": metadata,
        }
        if value:
            payload["value"] = value
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
        if response.status_code == 404:
            raise DirectLineConversationExpiredError(
                f"Direct Line conversation not found (expired): {conversation_id}"
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
    ) -> tuple[list[str], str | None, list[dict]]:
        timeout_seconds = self._settings.poll_timeout_seconds
        interval = self._settings.poll_interval_seconds

        start = asyncio.get_event_loop().time()
        seen_ids: set[str] = set()
        messages: list[str] = []
        current_watermark = watermark
        got_first_bot_reply = False
        pending_card_inputs: list[dict] = []

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

                attachment_types = [
                    a.content_type for a in activity.attachments if a.content_type
                ]
                if self._settings.debug_transcript_logging:
                    logger.debug(
                        "Direct Line activity received",
                        extra={
                            "extra": {
                                "activity_id": activity.id,
                                "type": activity.type,
                                "sender": activity.from_.id if activity.from_ else None,
                                "has_text": bool(activity.text),
                                "text_preview": (activity.text or "")[:120],
                                "attachment_count": len(activity.attachments),
                                "attachment_types": attachment_types,
                            }
                        },
                    )

                if activity.type != "message":
                    logger.debug(
                        "Skipping non-message activity",
                        extra={"extra": {"activity_id": activity.id, "type": activity.type}},
                    )
                    continue

                sender = activity.from_.id if activity.from_ else ""
                if sender == user_from_id:
                    continue

                for attachment in activity.attachments:
                    if (
                        attachment.content_type == "application/vnd.microsoft.card.adaptive"
                        and attachment.content
                    ):
                        card_inputs = DirectLineService._extract_card_inputs(attachment.content)
                        if card_inputs:
                            pending_card_inputs.extend(card_inputs)

                text = activity.text
                if not text:
                    for attachment in activity.attachments:
                        extracted = self._extract_attachment_text(attachment)
                        if extracted:
                            text = extracted
                            logger.info(
                                "Extracted fallback text from attachment",
                                extra={
                                    "extra": {
                                        "activity_id": activity.id,
                                        "content_type": attachment.content_type,
                                        "text_preview": text[:120],
                                    }
                                },
                            )
                            break

                if text:
                    messages.append(text)
                    new_bot_messages += 1
                else:
                    logger.warning(
                        "Bot message has no text and no extractable attachment content — dropped",
                        extra={
                            "extra": {
                                "activity_id": activity.id,
                                "attachment_types": attachment_types,
                            }
                        },
                    )

            if new_bot_messages > 0:
                got_first_bot_reply = True
            elif got_first_bot_reply:
                break

            await asyncio.sleep(interval)

        if not messages:
            raise DirectLineReceiveTimeoutError("No bot messages received within polling timeout")

        return messages, current_watermark, pending_card_inputs

    @staticmethod
    def _extract_attachment_text(attachment: DirectLineAttachment) -> str | None:
        """Extract readable plain text from a Bot Framework card attachment.

        Supports:
        - application/vnd.microsoft.card.adaptive  (Adaptive Card)
        - application/vnd.microsoft.card.hero      (Hero Card)
        - application/vnd.microsoft.card.thumbnail (Thumbnail Card)
        """
        content = attachment.content
        if not content or not attachment.content_type:
            return None

        ct = attachment.content_type

        if ct == "application/vnd.microsoft.card.adaptive":
            return DirectLineService._extract_adaptive_card_text(content)

        if ct in (
            "application/vnd.microsoft.card.hero",
            "application/vnd.microsoft.card.thumbnail",
        ):
            parts: list[str] = []
            if content.get("title"):
                parts.append(content["title"])
            if content.get("subtitle"):
                parts.append(content["subtitle"])
            if content.get("text"):
                parts.append(content["text"])
            return "\n".join(parts) if parts else None

        return None

    @staticmethod
    def _extract_adaptive_card_text(content: dict) -> str | None:
        """Recursively pull human-readable text out of an Adaptive Card body.

        Includes:
        - TextBlock text
        - FactSet entries
        - Input.* label + current value (or placeholder prompt)
        """
        lines: list[str] = []

        def _collect(elements: list) -> None:
            for element in elements:
                if not isinstance(element, dict):
                    continue
                elem_type = element.get("type", "")
                if elem_type == "TextBlock":
                    t = element.get("text", "")
                    if t:
                        lines.append(t)
                elif elem_type == "FactSet":
                    for fact in element.get("facts", []):
                        title = fact.get("title", "")
                        value = fact.get("value", "")
                        if title and value:
                            lines.append(f"{title}: {value}")
                        elif value:
                            lines.append(value)
                elif elem_type.startswith("Input."):
                    field_id = element.get("id", "")
                    label = element.get("label") or field_id
                    value = element.get("value")
                    placeholder = element.get("placeholder")
                    if label:
                        if value is not None and str(value).strip() != "":
                            lines.append(f"{label}: {value}")
                        elif placeholder:
                            lines.append(f"{label}: {placeholder}")
                elif elem_type in ("Container", "Column", "ColumnSet"):
                    _collect(element.get("items", []))
                    _collect(element.get("columns", []))

        _collect(content.get("body", []))
        return "\n".join(lines) if lines else None

    @staticmethod
    def _extract_card_inputs(content: dict) -> list[dict]:
        """Extract Input.* field definitions from an Adaptive Card body.

        If Action.Submit includes hidden "data", it is attached to each input entry so
        submit emulation can preserve required metadata.
        """
        inputs: list[dict] = []
        submit_action_data: dict = {}

        def _collect(elements: list) -> None:
            for element in elements:
                if not isinstance(element, dict):
                    continue
                elem_type = element.get("type", "")
                if elem_type.startswith("Input."):
                    field_id = element.get("id")
                    if field_id:
                        inputs.append(
                            {
                                "id": field_id,
                                "type": elem_type,
                                "label": element.get("label"),
                                "value": element.get("value"),
                                "placeholder": element.get("placeholder"),
                            }
                        )
                elif elem_type in ("Container", "Column", "ColumnSet"):
                    _collect(element.get("items", []) or element.get("columns", []))

        _collect(content.get("body", []))

        actions = content.get("actions", [])
        if isinstance(actions, list):
            for action in actions:
                if not isinstance(action, dict):
                    continue
                if action.get("type") == "Action.Submit":
                    raw_data = action.get("data")
                    if isinstance(raw_data, dict):
                        submit_action_data = dict(raw_data)
                    break

        if submit_action_data:
            for item in inputs:
                item["action_data"] = submit_action_data

        if not inputs and submit_action_data:
            inputs.append(
                {
                    "id": None,
                    "type": "Action.Submit",
                    "action_data": submit_action_data,
                }
            )

        return inputs