from __future__ import annotations

import httpx

from app.core.config import Settings
from app.core.exceptions import ZoomReplyError
from app.schemas.zoom import (
    ZoomChatMessageRequest,
    ZoomReplyBodyItem,
    ZoomReplyContent,
    ZoomReplyHead,
)


class ZoomChatService:
    """Sends plain-text responses back to Zoom Team Chat using the chatbot API."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self._settings = settings
        self._base = settings.zoom_chatbot_api_base.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=settings.request_timeout_seconds)

    def build_message_payload(
        self,
        to_jid: str,
        text: str,
        user_jid: str,
        account_id: str | None = None,
        thread_id: str | None = None,
    ) -> ZoomChatMessageRequest:
        if not self._settings.zoom_bot_jid:
            raise ZoomReplyError("ZOOM_BOT_JID is not configured")

        resolved_account_id = account_id or self._settings.zoom_account_id
        if not resolved_account_id:
            raise ZoomReplyError("Zoom account_id is missing for chatbot send")

        if not to_jid:
            raise ZoomReplyError("Zoom to_jid is missing for chatbot send")

        if not user_jid:
            raise ZoomReplyError("Zoom user_jid is missing for chatbot send")

        return ZoomChatMessageRequest(
            robot_jid=self._settings.zoom_bot_jid,
            to_jid=to_jid,
            account_id=resolved_account_id,
            user_jid=user_jid,
            is_markdown_support=True,
            content=ZoomReplyContent(
                head=ZoomReplyHead(text="Copilot"),
                body=[ZoomReplyBodyItem(type="message", text=text)],
            ),
            thread_id=thread_id,
        )

    async def send_text_message(
        self,
        access_token: str,
        to_jid: str,
        text: str,
        user_jid: str,
        account_id: str | None = None,
    ) -> None:
        payload = self.build_message_payload(
            to_jid=to_jid,
            text=text,
            user_jid=user_jid,
            account_id=account_id,
        )

        url = f"{self._base}/v2/im/chat/messages"
        response = await self._client.post(
            url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json=payload.model_dump(exclude_none=True),
        )

        if response.status_code >= 400:
            raise ZoomReplyError(
                f"Zoom chatbot message send failed with status {response.status_code}: {response.text}"
            )