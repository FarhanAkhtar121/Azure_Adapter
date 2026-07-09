from __future__ import annotations

import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from app.core.exceptions import InvalidSignatureError, MalformedZoomPayloadError
from app.core.logging import get_logger
from app.core.security import new_request_id
from app.schemas.zoom import ZoomWebhookPayload
from app.services.background_processor import BackgroundProcessor
from app.services.zoom_signature_service import ZoomSignatureService

logger = get_logger(__name__)
router = APIRouter(prefix="/api/zoom", tags=["zoom"])


def get_signature_service(request: Request) -> ZoomSignatureService:
    return request.app.state.signature_service


def get_background_processor(request: Request) -> BackgroundProcessor:
    return request.app.state.background_processor


@router.post("/webhook", status_code=status.HTTP_204_NO_CONTENT)
async def zoom_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    signature_service: ZoomSignatureService = Depends(get_signature_service),
    background_processor: BackgroundProcessor = Depends(get_background_processor),
) -> Response:
    request_id = request.headers.get("x-request-id") or new_request_id()
    raw_body = await request.body()

    try:
        signature_service.verify_signature(dict(request.headers), raw_body)
    except InvalidSignatureError as exc:
        logger.warning(
            "Invalid Zoom signature",
            extra={
                "extra": {
                    "request_id": request_id,
                    "reason": str(exc),
                    "hint": "Ensure ZOOM_SECRET_TOKEN matches the Verification Token in your Zoom app dashboard",
                }
            },
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature") from exc

    try:
        payload_dict = json.loads(raw_body)
        payload = ZoomWebhookPayload.model_validate(payload_dict)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON payload") from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed payload") from exc

    # Zoom endpoint URL validation — must respond with HTTP 200 + encrypted token.
    # This handshake occurs when a new webhook URL is registered in the Zoom dashboard.
    if payload.event == "endpoint.url_validation":
        plain_token = payload.payload.get("plainToken", "")
        encrypted_token = signature_service.compute_encrypted_token(plain_token)
        logger.info(
            "Zoom endpoint URL validation succeeded",
            extra={"extra": {"request_id": request_id}},
        )
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"plainToken": plain_token, "encryptedToken": encrypted_token},
        )

    logger.info(
        "Zoom webhook acknowledged",
        extra={
            "extra": {
                "request_id": request_id,
                "event": payload.event,
                "event_ts": payload.event_ts,
            }
        },
    )

    background_tasks.add_task(background_processor.process_event, payload_dict, request_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
