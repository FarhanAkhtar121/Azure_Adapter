# Zoom Team Chat <-> Microsoft Copilot Studio Middleware Adapter

Production-oriented FastAPI middleware adapter for integrating:

- Zoom Team Chat chatbot webhooks
- Microsoft Copilot Studio via Direct Line 3.0
- Zoom Chatbot API reply delivery

This implementation is optimized for Azure App Service first and remains container-friendly.

## Key v1 behaviors

- Acknowledges Zoom webhook requests immediately (HTTP 204) after signature + payload validation.
- Performs full Copilot round-trip asynchronously in background flow.
- Uses Direct Line 3.0 bearer token flow and polling-based receive path.
- Persists conversation mapping and watermark in SQLAlchemy async repository.
- Uses retries for transient token and polling failures.
- Sends plain text replies back to Zoom.

## Architecture

### Layers

- Presentation layer: FastAPI routes in `app/api/routes/`
- Application layer: services in `app/services/`
- Infrastructure layer: SQLAlchemy repository + HTTP clients + settings/logging

### Request flow

1. Zoom calls `POST /api/zoom/webhook`.
2. Route verifies `x-zm-signature` using HMAC with `ZOOM_SECRET_TOKEN`.
3. Route validates minimal payload and immediately returns 204.
4. Background processor normalizes event and resolves conversation mapping.
5. If token/mapping missing or expired, new Direct Line token/conversation is obtained.
6. User message is sent to Direct Line as an activity.
7. Service polls Direct Line activities with watermark until bot response(s) stabilize.
8. Bot text replies are sent to Zoom Team Chat API.
9. Watermark, last activity, and status are updated.

### Data model

`conversation_mappings` table fields:

- id
- zoom_user_id
- zoom_channel_id
- zoom_thread_id
- zoom_to_jid
- directline_conversation_id
- directline_token
- directline_token_expires_at
- watermark
- locale
- last_activity_at
- created_at
- updated_at
- last_zoom_event_id
- status
- metadata_json

## Project structure

```text
app/
  api/routes/
    health.py
    zoom_webhook.py
  core/
    config.py
    exceptions.py
    logging.py
    security.py
  models/
    db.py
    conversation_mapping.py
  repositories/
    conversation_repository.py
    sqlalchemy_conversation_repository.py
  schemas/
    common.py
    directline.py
    zoom.py
  services/
    background_processor.py
    copilot_token_service.py
    directline_service.py
    message_router_service.py
    zoom_auth_service.py
    zoom_chat_service.py
    zoom_signature_service.py
  main.py
tests/
requirements.txt
.env.example
README.md
```

## Configuration

Copy `.env.example` to `.env` and fill all required values.

Required env vars for full flow:

- `ZOOM_SECRET_TOKEN`
- `ZOOM_CLIENT_ID`
- `ZOOM_CLIENT_SECRET`
- `ZOOM_ACCOUNT_ID`
- `ZOOM_BOT_JID`
- `ZOOM_CHATBOT_API_BASE`
- `COPILOT_TOKEN_ENDPOINT`

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
cp .env.example .env
```

Run:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Test:

```bash
pytest -q
```

## Webhook testing notes (public HTTPS)

Zoom requires HTTPS endpoint. For local testing, use a tunnel (for example ngrok):

```bash
ngrok http 8000
```

Configure Zoom chatbot/event subscription webhook URL to:

`https://<ngrok-id>.ngrok.io/api/zoom/webhook`

## Sample webhook shape (defensive parser)

```json
{
  "event": "bot_notification",
  "event_ts": 1710000000,
  "payload": {
    "cmd": "hello",
    "user_id": "u123",
    "channel_id": "c123",
    "thread_id": "t123",
    "to_jid": "u123@xmpp.zoom.us",
    "locale": "en-US"
  }
}
```

## Direct Line polling flow (v1)

- Send user activity: `POST /v3/directline/conversations/{conversationId}/activities`
- Poll bot activities: `GET /v3/directline/conversations/{conversationId}/activities?watermark=...`
- Stop condition: at least one bot message arrived and a subsequent poll is quiet.

## Error handling

Custom exceptions include:

- invalid signature
- malformed payload
- token failures
- Direct Line send/receive failures
- Zoom send failures
- repository failures

Webhook route behavior:

- invalid signature -> `401`
- malformed payload -> `400`
- otherwise immediate `204`

## Security and observability

- Constant-time signature comparison via `hmac.compare_digest`
- Strict pydantic validation at ingress and Direct Line token parsing
- Structured JSON logs with request correlation id
- Sensitive values (tokens/secrets) not logged
- Timeouts on all outbound HTTP requests

## Limitations in v1

- In-process background tasks are not durable across process restarts
- Polling receive path only (no WebSocket path yet)
- SQLite is intended for local/small deployments
- Zoom payload diversity is handled defensively but not exhaustive

## Roadmap

- Direct Line WebSocket receive support
- Durable queueing with Azure Queue Storage or Service Bus
- Repository adapters for PostgreSQL and Cosmos DB
- Rich Zoom card/message templates
- Distributed tracing/metrics with App Insights/OpenTelemetry

## Container-friendliness

The app follows 12-factor style env-driven config and async HTTP/DB patterns, so it can run in containers with no architecture changes. `uvicorn app.main:app --host 0.0.0.0 --port $APP_PORT` is sufficient for container entrypoint.
