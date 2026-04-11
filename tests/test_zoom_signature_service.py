import hashlib
import hmac

import pytest

from app.core.config import Settings
from app.core.exceptions import InvalidSignatureError
from app.services.zoom_signature_service import ZoomSignatureService


def _build_headers(secret: str, body: bytes, timestamp: str = "1710000000") -> dict[str, str]:
    msg = f"v0:{timestamp}:{body.decode('utf-8')}".encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return {
        "x-zm-request-timestamp": timestamp,
        "x-zm-signature": f"v0={sig}",
    }


def _settings() -> Settings:
    return Settings(ZOOM_SECRET_TOKEN="topsecret")


def test_verify_signature_success() -> None:
    service = ZoomSignatureService(_settings())
    body = b'{"event":"bot_notification","payload":{"cmd":"hello"}}'
    headers = _build_headers("topsecret", body)

    assert service.verify_signature(headers, body) is True


def test_verify_signature_failure() -> None:
    service = ZoomSignatureService(_settings())
    body = b'{"event":"bot_notification"}'
    headers = {
        "x-zm-request-timestamp": "1710000000",
        "x-zm-signature": "v0=invalid",
    }

    with pytest.raises(InvalidSignatureError):
        service.verify_signature(headers, body)
