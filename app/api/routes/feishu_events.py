import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.security import (
    decrypt_feishu_payload_if_needed,
    verify_feishu_event_freshness,
    verify_feishu_token,
)
from app.db.session import get_db
from app.services.feishu_message_service import get_external_event_id, process_feishu_message_event

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/feishu", tags=["feishu"])


@router.post("/events")
async def handle_feishu_event(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Feishu event callback entrypoint."""

    payload = await request.json()
    try:
        payload = decrypt_feishu_payload_if_needed(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    challenge = payload.get("challenge")
    if challenge:
        if not verify_feishu_token(payload.get("token")):
            raise HTTPException(status_code=403, detail="invalid verification token")
        return {"challenge": challenge}

    if not verify_feishu_token(payload.get("token")):
        raise HTTPException(status_code=403, detail="invalid verification token")

    header = payload.get("header") or {}
    if not verify_feishu_event_freshness(header):
        raise HTTPException(status_code=403, detail="stale or missing Feishu event timestamp")
    event = payload.get("event") or {}
    event_type = header.get("event_type") or payload.get("type")
    external_event_id = get_external_event_id(header, event)

    try:
        if event_type == "im.message.receive_v1":
            processed = await process_feishu_message_event(
                db,
                external_event_id=external_event_id,
                event_type=event_type,
                event=event,
                raw_payload=payload,
            )
            if not processed:
                return {"code": 0, "msg": "duplicate"}
        else:
            logger.info("feishu_event_ignored event_type=%s", event_type)
        return {"code": 0, "msg": "success"}
    except Exception as exc:
        logger.warning("feishu_event_processing_failed event_type=%s error_type=%s", event_type, type(exc).__name__)
        raise
