from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

import lark_oapi as lark
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.services.feishu_message_service import get_external_event_id, process_feishu_message_event

logger = logging.getLogger(__name__)


def main() -> None:
    """Start the Feishu long-connection event listener."""

    setup_logging()
    init_db()

    settings = get_settings()
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        raise RuntimeError("请先在 .env 填写 FEISHU_APP_ID 和 FEISHU_APP_SECRET")

    event_handler = (
        lark.EventDispatcherHandler.builder(
            settings.feishu_encrypt_key,
            settings.feishu_verification_token,
            lark.LogLevel.INFO,
        )
        .register_p2_im_message_receive_v1(_on_message_receive)
        .build()
    )

    logger.info("feishu_long_connection_starting")
    client = lark.ws.Client(
        settings.feishu_app_id,
        settings.feishu_app_secret,
        log_level=lark.LogLevel.INFO,
        event_handler=event_handler,
    )
    client.start()


def _on_message_receive(data: P2ImMessageReceiveV1) -> None:
    """SDK callback for im.message.receive_v1."""

    # The SDK invokes callbacks inside its event loop. Run our async reply flow
    # in a short-lived thread so the long-connection ack is not blocked.
    thread = threading.Thread(target=lambda: asyncio.run(_process_sdk_message(data)), daemon=True)
    thread.start()


async def _process_sdk_message(data: P2ImMessageReceiveV1) -> None:
    payload = _sdk_event_to_payload(data)
    header = payload.get("header") or {}
    event = payload.get("event") or {}
    event_type = header.get("event_type") or "im.message.receive_v1"
    external_event_id = get_external_event_id(header, event)

    db = SessionLocal()
    try:
        await process_feishu_message_event(
            db,
            external_event_id=external_event_id,
            event_type=event_type,
            event=event,
            raw_payload=payload,
            source="feishu_long_connection",
        )
    except Exception as exc:
        logger.warning("feishu_long_connection_event_failed error_type=%s", type(exc).__name__)
    finally:
        db.close()


def _sdk_event_to_payload(data: P2ImMessageReceiveV1) -> dict[str, Any]:
    header = _object_to_dict(data.header)
    event = _object_to_dict(data.event)
    return {
        "schema": data.schema or "2.0",
        "header": header,
        "event": event,
    }


def _object_to_dict(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_object_to_dict(item) for item in value]
    if isinstance(value, dict):
        return {key: _object_to_dict(item) for key, item in value.items()}
    if hasattr(value, "__dict__"):
        result: dict[str, Any] = {}
        for key, item in value.__dict__.items():
            if key.startswith("_") or item is None:
                continue
            result[key] = _object_to_dict(item)
        return result
    return str(value)


if __name__ == "__main__":
    main()
