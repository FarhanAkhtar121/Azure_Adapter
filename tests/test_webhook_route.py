import hashlib
import hmac

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def _signature(secret: str, body: bytes, ts: str) -> str:
    msg = f"v0:{ts}:{body.decode('utf-8')}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return f"v0={digest}"


def _app() -> TestClient:
    settings = Settings(
        APP_ENV="test",
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
        ZOOM_SECRET_TOKEN="secret",
        COPILOT_TOKEN_ENDPOINT="https://example.test/token",
        ZOOM_CLIENT_ID="id",
        ZOOM_CLIENT_SECRET="secret",
        ZOOM_ACCOUNT_ID="acc",
        ZOOM_BOT_JID="bot@xmpp.zoom.us",
    )
    app = create_app(settings)

    class StubProcessor:
        called = False

        async def process_event(self, raw_payload: dict, request_id: str):
            self.called = True

    app.state.background_processor = StubProcessor()
    return TestClient(app)


def test_webhook_acknowledges_quickly() -> None:
    client = _app()
    body = b'{"event":"bot_notification","payload":{"cmd":"hello","user_id":"u1","channel_id":"c1"}}'
    ts = "1710000000"

    response = client.post(
        "/api/zoom/webhook",
        content=body,
        headers={
            "x-zm-request-timestamp": ts,
            "x-zm-signature": _signature("secret", body, ts),
            "content-type": "application/json",
        },
    )
    assert response.status_code == 204


def test_webhook_invalid_signature_returns_401() -> None:
    client = _app()
    body = b'{"event":"bot_notification","payload":{"cmd":"hello"}}'
    ts = "1710000000"

    response = client.post(
        "/api/zoom/webhook",
        content=body,
        headers={
            "x-zm-request-timestamp": ts,
            "x-zm-signature": "v0=bad",
            "content-type": "application/json",
        },
    )

    assert response.status_code == 401
