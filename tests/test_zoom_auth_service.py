import httpx
import pytest

from app.core.config import Settings
from app.core.exceptions import ZoomReplyError
from app.services.zoom_auth_service import ZoomAuthService


@pytest.mark.asyncio
async def test_fetch_token_prefers_s2s_credentials() -> None:
    observed: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        observed["query"] = str(request.url)
        observed["auth"] = request.headers["Authorization"]
        return httpx.Response(200, json={"access_token": "token-1", "expires_in": 3600})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = Settings(
        ZOOM_CHATBOT_API_BASE="https://api.zoom.us",
        ZOOM_CLIENT_ID="legacy-id",
        ZOOM_CLIENT_SECRET="legacy-secret",
        ZOOM_ACCOUNT_ID="legacy-account",
        ZOOM_S2S_CLIENT_ID="s2s-id",
        ZOOM_S2S_CLIENT_SECRET="s2s-secret",
        ZOOM_S2S_ACCOUNT_ID="s2s-account",
    )

    service = ZoomAuthService(settings=settings, client=client)

    token, expires_in = await service._fetch_token()

    assert token == "token-1"
    assert expires_in == 3600
    assert "account_id=s2s-account" in observed["query"]
    assert observed["auth"] == "Basic czJzLWlkOnMycy1zZWNyZXQ="

    await client.aclose()


@pytest.mark.asyncio
async def test_fetch_token_falls_back_to_legacy_credentials() -> None:
    observed: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        observed["query"] = str(request.url)
        observed["auth"] = request.headers["Authorization"]
        return httpx.Response(200, json={"access_token": "token-2", "expires_in": 1800})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = Settings(
        ZOOM_CHATBOT_API_BASE="https://api.zoom.us",
        ZOOM_CLIENT_ID="legacy-id",
        ZOOM_CLIENT_SECRET="legacy-secret",
        ZOOM_ACCOUNT_ID="legacy-account",
        ZOOM_S2S_CLIENT_ID="",
        ZOOM_S2S_CLIENT_SECRET="",
        ZOOM_S2S_ACCOUNT_ID="",
    )

    service = ZoomAuthService(settings=settings, client=client)

    token, expires_in = await service._fetch_token()

    assert token == "token-2"
    assert expires_in == 1800
    assert "account_id=legacy-account" in observed["query"]
    assert observed["auth"] == "Basic bGVnYWN5LWlkOmxlZ2FjeS1zZWNyZXQ="

    await client.aclose()


def test_resolve_oauth_credentials_rejects_partial_s2s_configuration() -> None:
    settings = Settings(
        ZOOM_S2S_CLIENT_ID="s2s-id",
        ZOOM_S2S_CLIENT_SECRET="",
        ZOOM_S2S_ACCOUNT_ID="",
        ZOOM_CLIENT_ID="legacy-id",
        ZOOM_CLIENT_SECRET="legacy-secret",
        ZOOM_ACCOUNT_ID="legacy-account",
    )

    service = ZoomAuthService(settings=settings)

    with pytest.raises(ZoomReplyError, match="partially configured"):
        service._resolve_oauth_credentials()