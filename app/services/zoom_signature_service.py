from __future__ import annotations

import hmac
import hashlib

from app.core.config import Settings
from app.core.exceptions import InvalidSignatureError


class ZoomSignatureService:
    """Validates Zoom webhook signatures using the configured secret token."""

    def __init__(self, settings: Settings):
        self._secret = settings.zoom_secret_token

    def verify_signature(self, headers: dict, raw_body: bytes) -> bool:
        if not self._secret:
            raise InvalidSignatureError("ZOOM_SECRET_TOKEN is not configured")

        lowered = {k.lower(): v for k, v in headers.items()}
        incoming_signature = lowered.get("x-zm-signature")
        timestamp = lowered.get("x-zm-request-timestamp")

        if not incoming_signature or not timestamp:
            raise InvalidSignatureError("Missing Zoom signature headers")

        body_text = raw_body.decode("utf-8")
        message = f"v0:{timestamp}:{body_text}".encode("utf-8")
        expected_hash = hmac.new(self._secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
        expected_signature = f"v0={expected_hash}"

        if not hmac.compare_digest(expected_signature, incoming_signature):
            raise InvalidSignatureError("Invalid Zoom webhook signature")

        return True
