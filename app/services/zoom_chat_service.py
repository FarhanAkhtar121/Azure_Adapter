from __future__ import annotations

import httpx

from app.core.config import Settings
from app.core.exceptions import ZoomReplyError
from app.schemas.zoom import ZoomChatMessageRequest, ZoomReplyBodyItem, ZoomReplyContent


class ZoomChatService:
    """Sends plain-text responses back to Zoom Team Chat."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self._settings = settings
        self._base = settings.zoom_chatbot_api_base.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=settings.request_timeout_seconds)

    def build_message_payload(
        self,
        to_jid: str,
        text: str,
        thread_id: str | None = None,
        user_jid: str | None = None,
    ) -> ZoomChatMessageRequest:
        if not self._settings.zoom_bot_jid:
            raise ZoomReplyError("ZOOM_BOT_JID is not configured")

        return ZoomChatMessageRequest(
            robot_jid=self._settings.zoom_bot_jid,
            to_jid=to_jid,
            account_id=self._settings.zoom_account_id or None,
            user_jid=user_jid,
            thread_id=thread_id,
            content=ZoomReplyContent(
                head={"text": "Copilot"},
                body=[ZoomReplyBodyItem(text=text)],
            ),
        )

    async def send_text_message(
        self,
        access_token: str,
        to_jid: str,
        text: str,
        thread_id: str | None = None,
        user_jid: str | None = None,
    ) -> None:
        payload = self.build_message_payload(
            to_jid=to_jid,
            text=text,
            thread_id=thread_id,
            user_jid=user_jid,
        )

        url = f"{self._base}/v2/im/chat/messages"
        response = await self._client.post(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            json=payload.model_dump(exclude_none=True),
        )
        if response.status_code >= 400:
            raise ZoomReplyError(f"Zoom message send failed with status {response.status_code}")
