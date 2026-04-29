from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from app.core.config import Settings
from app.core.exceptions import DirectLineConversationExpiredError, DirectLineReceiveTimeoutError, DirectLineSendError
from app.core.logging import get_logger
from app.models.conversation_mapping import ConversationMapping
from app.schemas.directline import (
    DirectLineActivitiesResponse,
    DirectLineAttachment,
    DirectLineCardAction,
)
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
    ) -> tuple[list[str], list[dict | None], str | None, list[dict]]:
        timeout_seconds = self._settings.poll_timeout_seconds
        interval = self._settings.poll_interval_seconds

        start = asyncio.get_event_loop().time()
        seen_ids: set[str] = set()
        messages: list[str] = []
        zoom_cards: list[dict | None] = []
        current_watermark = watermark
        got_first_bot_reply = False
        idle_polls_after_first_reply = 0
        # Allow a few idle polls after first reply because Copilot can emit the
        # follow-up adaptive card in a later activity batch.
        max_idle_polls_after_first_reply = 4
        pending_card_inputs: list[dict] = []
        seen_reply_signatures: set[str] = set()

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

                activity_zoom_card: dict | None = None
                for attachment in activity.attachments:
                    if (
                        attachment.content_type == "application/vnd.microsoft.card.adaptive"
                        and attachment.content
                    ):
                        card_inputs = DirectLineService._extract_card_inputs(attachment.content)
                        if card_inputs:
                            pending_card_inputs.extend(card_inputs)
                        if activity_zoom_card is None:
                            built = DirectLineService._build_zoom_card_content(
                                attachment.content, card_inputs
                            )
                            if built:
                                activity_zoom_card = built

                if activity_zoom_card is None and activity.suggested_actions:
                    activity_zoom_card = DirectLineService._build_zoom_suggested_actions_content(
                        activity.text,
                        activity.suggested_actions.actions,
                    )

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
                    card_signature = (
                        json.dumps(activity_zoom_card, sort_keys=True)
                        if activity_zoom_card is not None
                        else ""
                    )
                    reply_signature = f"{text}\n{card_signature}"
                    if reply_signature in seen_reply_signatures:
                        logger.info(
                            "Skipping duplicate bot reply activity in same poll cycle",
                            extra={"extra": {"activity_id": activity.id}},
                        )
                        continue

                    seen_reply_signatures.add(reply_signature)
                    messages.append(text)
                    zoom_cards.append(activity_zoom_card)
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
                idle_polls_after_first_reply = 0
            elif got_first_bot_reply:
                idle_polls_after_first_reply += 1
                if idle_polls_after_first_reply >= max_idle_polls_after_first_reply:
                    break

            await asyncio.sleep(interval)

        if not messages:
            raise DirectLineReceiveTimeoutError("No bot messages received within polling timeout")

        return messages, zoom_cards, current_watermark, pending_card_inputs

    @staticmethod
    def _build_zoom_suggested_actions_content(
        text: str | None,
        actions: list[DirectLineCardAction],
    ) -> dict | None:
        """Build a Zoom interactive card for Direct Line suggested actions."""
        if not actions:
            return None

        button_items: list[dict] = []
        for action in actions:
            label = (action.title or action.text or "").strip()
            value = (action.value or action.text or action.title or "").strip()
            if not label or not value:
                continue
            button_items.append({"text": label, "value": value})

        if not button_items:
            return None

        prompt = (text or "Please choose an option").strip()
        if not prompt:
            prompt = "Please choose an option"

        return {
            "head": {"text": "Copilot"},
            "body": [
                {
                    "type": "section",
                    "layout": "horizontal",
                    "sections": [
                        {"type": "message", "text": prompt},
                        {"type": "actions", "items": button_items},
                    ],
                }
            ],
        }

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
                                "isMultiline": element.get("isMultiline", False),
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

    @staticmethod
    def _build_zoom_card_content(content: dict, card_inputs: list[dict]) -> dict | None:
        """Transform an Adaptive Card into a Zoom Team Chat interactive message content dict.

        Mapping:
        - First TextBlock       → head.text
        - Second TextBlock      → head.sub_head.text
        - Further TextBlocks    → body message section
        - FactSet               → body fields section
        - Input.* with value    → body fields display + plain_text_input
        - Input.* no value      → body plain_text_input only
        - Action.Submit         → body actions button (encodes submit intent as JSON value)
        - Action.OpenUrl        → body message section with markdown link

        Returns None if the card cannot be meaningfully represented (e.g., no title).
        """
        body: list[dict] = []
        head_text: str = ""
        sub_head_text: str = ""
        extra_text_lines: list[str] = []
        field_display_items: list[dict] = []

        def _walk(elements: list) -> None:
            nonlocal head_text, sub_head_text
            for element in elements:
                if not isinstance(element, dict):
                    continue
                elem_type = element.get("type", "")
                if elem_type == "TextBlock":
                    t = element.get("text", "").strip()
                    if not t:
                        continue
                    if not head_text:
                        head_text = t
                    elif not sub_head_text:
                        sub_head_text = t
                    else:
                        extra_text_lines.append(t)
                elif elem_type == "FactSet":
                    for fact in element.get("facts", []):
                        title = fact.get("title", "")
                        value = fact.get("value", "")
                        if title and value:
                            field_display_items.append({"key": title, "value": str(value), "short": True})
                elif elem_type in ("Container", "Column", "ColumnSet"):
                    _walk(element.get("items", []))
                    _walk(element.get("columns", []))

        _walk(content.get("body", []))

        if not head_text:
            return None

        has_prefilled_inputs = any(
            inp.get("id") and inp.get("value") is not None and str(inp.get("value")).strip() != ""
            for inp in card_inputs
        )

        # Collect input field current values for display
        if not has_prefilled_inputs:
            for inp in card_inputs:
                inp_id = inp.get("id")
                if not inp_id:
                    continue
                label = inp.get("label") or inp_id
                value = inp.get("value")
                if value is not None and str(value).strip():
                    field_display_items.append({"key": label, "value": str(value), "short": True})

        # Add extra body text as a message section
        if extra_text_lines and not has_prefilled_inputs:
            body.append({
                "type": "section",
                "sections": [{"type": "message", "text": "\n".join(extra_text_lines)}],
            })

        # Add current-value display for FactSet entries and pre-filled inputs
        if field_display_items and not has_prefilled_inputs:
            body.append({
                "type": "section",
                "sections": [{"type": "fields", "items": field_display_items}],
            })

        # Add a plain_text_input for each editable input field
        for inp in card_inputs:
            inp_id = inp.get("id")
            if not inp_id:
                continue
            label = inp.get("label") or inp_id
            value = inp.get("value") or ""
            placeholder = inp.get("placeholder") or ""
            is_multiline = bool(inp.get("isMultiline", False))
            body.append({
                "type": "plain_text_input",
                "action_id": inp_id,
                "text": label,
                "value": str(value),
                "placeholder": placeholder,
                "multiline": is_multiline,
                "min_length": 0,
                "max_length": 2000,
            })

        # Collect hidden Action.Submit data from inputs (already extracted)
        action_data: dict = {}
        for inp in card_inputs:
            raw = inp.get("action_data")
            if isinstance(raw, dict):
                action_data.update(raw)

        # Process actions: submit buttons + open-url markdown links
        action_buttons: list[dict] = []
        open_url_links: list[str] = []

        for action in content.get("actions", []):
            if not isinstance(action, dict):
                continue
            action_type = action.get("type")
            title = action.get("title", "Submit")
            if action_type == "Action.Submit":
                button_value = json.dumps(
                    {
                        "zoom_action": "submit_card",
                        "action_data": action_data,
                    },
                    separators=(",", ":"),
                )
                action_buttons.append({"text": title, "value": button_value})
            elif action_type == "Action.OpenUrl":
                url = action.get("url", "")
                if url:
                    open_url_links.append(f"[{title}]({url})")

        if open_url_links:
            body.append({
                "type": "section",
                "sections": [{"type": "message", "text": "  ".join(open_url_links)}],
            })

        if action_buttons:
            has_editable_inputs = any(inp.get("id") for inp in card_inputs)
            prompt = (
                "Type edited values to change fields, then press Submit"
                if has_editable_inputs
                else "Press Submit when ready"
            )
            body.append({
                "type": "section",
                "layout": "horizontal",
                "sections": [
                    {"type": "message", "text": prompt},
                    {"type": "actions", "items": action_buttons},
                ],
            })

        head: dict = {"text": head_text}
        if sub_head_text:
            head["sub_head"] = {"text": sub_head_text}

        return {"head": head, "body": body}