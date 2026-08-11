from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.adapters.feishu_bitable import (
    batch_create_records,
    candidate_case_to_bitable_fields,
    create_record,
    update_record,
    workflow_node_to_bitable_fields,
)
from app.core.config import get_settings
from app.models.onboarding import CandidateCase, WorkflowNode

logger = logging.getLogger(__name__)


async def sync_case_to_bitable(case: CandidateCase, db: Session | None = None) -> dict[str, Any]:
    """Sync candidate summary and workflow nodes to Feishu Bitable when configured."""

    settings = get_settings()
    if not settings.feishu_base_app_token or not settings.feishu_candidate_table_id:
        return {"status": "skipped", "reason": "Feishu Bitable app_token/table_id not configured"}
    result: dict[str, Any] = {"status": "success"}
    try:
        fields = candidate_case_to_bitable_fields(case)
        if case.feishu_bitable_record_id:
            result["candidate"] = await update_record(
                settings.feishu_base_app_token,
                settings.feishu_candidate_table_id,
                case.feishu_bitable_record_id,
                fields,
            )
            result["candidate_action"] = "updated"
        else:
            result["candidate"] = await create_record(
                settings.feishu_base_app_token,
                settings.feishu_candidate_table_id,
                fields,
            )
            case.feishu_bitable_record_id = _extract_record_id(result["candidate"])
            result["candidate_action"] = "created"

        if settings.feishu_node_table_id:
            result["nodes"] = await sync_nodes_to_bitable(case.nodes, settings.feishu_base_app_token, settings.feishu_node_table_id)
        else:
            result["nodes"] = {"status": "skipped", "reason": "FEISHU_NODE_TABLE_ID not configured"}
        if db:
            db.commit()
        return result
    except Exception as exc:
        if db:
            db.rollback()
        logger.warning("bitable_case_sync_failed case_id=%s error_type=%s", case.id, type(exc).__name__)
        return {"status": "failed", "reason": type(exc).__name__}


async def sync_node_to_bitable(node: WorkflowNode) -> dict[str, Any]:
    """Sync a single workflow node when it already has a Bitable record id."""

    settings = get_settings()
    if not settings.feishu_base_app_token or not settings.feishu_node_table_id:
        return {"status": "skipped", "reason": "FEISHU_NODE_TABLE_ID not configured"}
    if not node.feishu_bitable_record_id:
        return {"status": "skipped", "reason": "node bitable record id not configured"}
    try:
        result = await update_record(
            settings.feishu_base_app_token,
            settings.feishu_node_table_id,
            node.feishu_bitable_record_id,
            workflow_node_to_bitable_fields(node),
        )
        return {"status": "success", "action": "updated", "result": result}
    except Exception as exc:
        logger.warning(
            "bitable_node_sync_failed node_id=%s case_id=%s error_type=%s",
            node.id,
            node.case_id,
            type(exc).__name__,
        )
        return {"status": "failed", "reason": type(exc).__name__}


async def sync_nodes_to_bitable(nodes: list[WorkflowNode], app_token: str, table_id: str) -> dict[str, Any]:
    """Create missing node records and update existing node records."""

    created_nodes = [node for node in nodes if not node.feishu_bitable_record_id]
    updated_nodes = [node for node in nodes if node.feishu_bitable_record_id]
    result: dict[str, Any] = {"created": 0, "updated": 0, "create_batches": [], "update_results": []}

    if created_nodes:
        records = [workflow_node_to_bitable_fields(node) for node in created_nodes]
        create_results = await batch_create_records(app_token, table_id, records)
        result["create_batches"] = create_results
        created_record_ids = _extract_batch_record_ids(create_results)
        for node, record_id in zip(created_nodes, created_record_ids):
            node.feishu_bitable_record_id = record_id
        result["created"] = len(created_record_ids)

    for node in updated_nodes:
        update_result = await update_record(
            app_token,
            table_id,
            node.feishu_bitable_record_id,
            workflow_node_to_bitable_fields(node),
        )
        result["update_results"].append(update_result)
    result["updated"] = len(updated_nodes)
    return result


def _extract_record_id(response: dict[str, Any]) -> str | None:
    record = ((response.get("data") or {}).get("record") or {})
    return record.get("record_id") or record.get("id")


def _extract_batch_record_ids(responses: list[dict[str, Any]]) -> list[str]:
    record_ids: list[str] = []
    for response in responses:
        records = ((response.get("data") or {}).get("records") or [])
        for record in records:
            record_id = record.get("record_id") or record.get("id")
            if record_id:
                record_ids.append(record_id)
    return record_ids
