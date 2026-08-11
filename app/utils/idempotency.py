from __future__ import annotations

import json
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.onboarding import ProcessEventLog


def claim_event(
    db: Session,
    *,
    external_event_id: str,
    event_type: str | None,
    payload: dict[str, Any],
    source: str = "feishu_event",
) -> bool:
    """Claim an event and return False only when it already succeeded."""

    existing = (
        db.query(ProcessEventLog)
        .filter(ProcessEventLog.external_event_id == external_event_id)
        .one_or_none()
    )
    if existing:
        if existing.status == "success":
            return False
        existing.source = source
        existing.event_type = event_type
        existing.payload_json = _stored_payload(payload)
        existing.status = "processing"
        existing.error_message = None
        db.commit()
        return True

    event_log = ProcessEventLog(
        source=source,
        external_event_id=external_event_id,
        event_type=event_type,
        payload_json=_stored_payload(payload),
        status="processing",
    )
    db.add(event_log)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return False
    return True


def mark_event_status(
    db: Session,
    *,
    external_event_id: str,
    status: str,
    error_message: str | None = None,
) -> None:
    """Update processing status for an idempotency event record."""

    event_log = (
        db.query(ProcessEventLog)
        .filter(ProcessEventLog.external_event_id == external_event_id)
        .one_or_none()
    )
    if event_log:
        event_log.status = status
        event_log.error_message = error_message
        db.commit()


def _stored_payload(payload: dict[str, Any]) -> str | None:
    """Avoid persisting message text and identifiers unless explicitly enabled."""

    if not get_settings().store_event_payloads:
        return None
    return json.dumps(payload, ensure_ascii=False)
