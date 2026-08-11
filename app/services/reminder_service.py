from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any

from sqlalchemy.orm import Session, selectinload

from app.adapters.feishu_bitable import update_record, workflow_node_to_bitable_fields
from app.adapters.feishu_im import overdue_node_card, overdue_node_guidance, send_interactive_card
from app.core.config import get_settings
from app.models.onboarding import CandidateCase, WorkflowNode
from app.services.reminder_copy import generate_overdue_guidance
from app.services.onboarding_workflow import scan_overdue_nodes
from app.utils.feishu_links import candidate_materials_url, candidate_node_confirm_url, hr_dashboard_url
from app.utils.idempotency import claim_event, mark_event_status

logger = logging.getLogger(__name__)

CardSender = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
HR_PROBATION_EMAIL_NODE_NAMES = {"probation_month_3", "probation_month_4"}


async def scan_overdue_and_notify(
    db: Session,
    *,
    today: date | None = None,
    send_card: CardSender = send_interactive_card,
    sync_bitable: bool = True,
) -> dict[str, Any]:
    """Scan overdue nodes, sync HR table state, and notify responsible Feishu users once per day."""

    changed_nodes = scan_overdue_nodes(db, today=today)
    node_ids = [node.id for node in changed_nodes]
    if not node_ids:
        return {"overdue_count": 0, "notified_count": 0, "skipped_count": 0, "node_ids": []}

    nodes = (
        db.query(WorkflowNode)
        .options(selectinload(WorkflowNode.case))
        .filter(WorkflowNode.id.in_(node_ids))
        .all()
    )
    run_date = today or date.today()
    notified_count = 0
    skipped_count = 0
    failed: list[dict[str, Any]] = []

    for node in nodes:
        event_id = f"overdue-reminder:{node.id}:{run_date.isoformat()}:{node.escalation_level}"
        payload = {
            "case_id": node.case_id,
            "node_id": node.id,
            "node_name": node.node_name,
            "overdue_days": node.overdue_days,
            "escalation_level": node.escalation_level,
        }
        if not claim_event(db, external_event_id=event_id, event_type="workflow_node_overdue", payload=payload, source="scheduler"):
            skipped_count += 1
            continue

        try:
            if sync_bitable:
                await _sync_node_to_bitable(node)
            owner_recipients = _resolve_recipients(node)
            candidate_recipient = _resolve_candidate_recipient(node)
            if candidate_recipient:
                owner_recipients = [open_id for open_id in owner_recipients if open_id != candidate_recipient]
            if not owner_recipients and not candidate_recipient:
                logger.info("overdue_reminder_no_recipient node_id=%s case_id=%s", node.id, node.case_id)
                mark_event_status(db, external_event_id=event_id, status="success")
                skipped_count += 1
                continue

            is_probation_email = node.node_name in HR_PROBATION_EMAIL_NODE_NAMES
            owner_audience = "hr_probation_email" if is_probation_email else "owner"
            owner_guidance = await generate_overdue_guidance(
                candidate_name=node.case.candidate_name,
                node_name=node.node_name,
                due_date=node.due_date.isoformat(),
                overdue_days=node.overdue_days,
                audience=owner_audience,
                fallback=overdue_node_guidance(node.node_name, owner_audience),
                manager_label=_manager_label(node.case) if is_probation_email else None,
            )
            owner_card = overdue_node_card(
                candidate_name=node.case.candidate_name,
                node_name=node.node_name,
                due_date=node.due_date.isoformat(),
                overdue_days=node.overdue_days,
                escalation_level=node.escalation_level,
                action_url=hr_dashboard_url(),
                audience=owner_audience,
                manager_label=_manager_label(node.case) if is_probation_email else None,
                guidance=owner_guidance,
            )
            for open_id in owner_recipients:
                await send_card(open_id, owner_card)
                notified_count += 1

            if candidate_recipient:
                candidate_guidance = await generate_overdue_guidance(
                    candidate_name=node.case.candidate_name,
                    node_name=node.node_name,
                    due_date=node.due_date.isoformat(),
                    overdue_days=node.overdue_days,
                    audience="candidate",
                    fallback=overdue_node_guidance(node.node_name, "candidate"),
                )
                candidate_card = overdue_node_card(
                    candidate_name=node.case.candidate_name,
                    node_name=node.node_name,
                    due_date=node.due_date.isoformat(),
                    overdue_days=node.overdue_days,
                    escalation_level=node.escalation_level,
                    action_url=_candidate_action_url(node),
                    audience="candidate",
                    guidance=candidate_guidance,
                    action_text=_candidate_action_text(node.node_name),
                )
                await send_card(candidate_recipient, candidate_card)
                notified_count += 1
            mark_event_status(db, external_event_id=event_id, status="success")
        except Exception as exc:
            error_type = type(exc).__name__
            logger.warning("overdue_reminder_failed node_id=%s case_id=%s error_type=%s", node.id, node.case_id, error_type)
            mark_event_status(db, external_event_id=event_id, status="failed", error_message=error_type)
            failed.append({"node_id": node.id, "reason": error_type})

    return {
        "overdue_count": len(nodes),
        "notified_count": notified_count,
        "skipped_count": skipped_count,
        "node_ids": node_ids,
        "failed": failed,
    }


def _resolve_recipients(node: WorkflowNode) -> list[str]:
    case = node.case
    if node.node_name in HR_PROBATION_EMAIL_NODE_NAMES:
        hr_candidates = [case.hrbp_open_id, node.collaborator_open_id]
        recipients = _unique_open_ids(hr_candidates)
        return recipients or _unique_open_ids([node.owner_open_id])

    candidates = [node.owner_open_id, node.collaborator_open_id]
    if node.escalation_level >= 2:
        candidates.extend([case.hrbp_open_id, case.manager_open_id])
    return _unique_open_ids(candidates)


def _resolve_candidate_recipient(node: WorkflowNode) -> str | None:
    if node.node_name in HR_PROBATION_EMAIL_NODE_NAMES:
        return None
    return node.case.feishu_open_id


def _candidate_action_url(node: WorkflowNode) -> str:
    if node.node_name == "document_submission":
        return candidate_materials_url(node.case_id)
    return candidate_node_confirm_url(node.case_id, node.node_name)


def _candidate_action_text(node_name: str) -> str:
    if node_name == "document_submission":
        return "进入工作台处理材料"
    return "进入工作台确认节点"


def _manager_label(case: CandidateCase) -> str:
    manager = case.manager_open_id or "未填写直属经理"
    if case.department:
        return f"{case.department}负责人（{manager}）"
    return manager


def _unique_open_ids(candidates: list[str | None]) -> list[str]:
    recipients: list[str] = []
    for open_id in candidates:
        if open_id and open_id not in recipients:
            recipients.append(open_id)
    return recipients


async def _sync_node_to_bitable(node: WorkflowNode) -> None:
    settings = get_settings()
    if not settings.feishu_base_app_token or not settings.feishu_node_table_id or not node.feishu_bitable_record_id:
        return
    await update_record(
        settings.feishu_base_app_token,
        settings.feishu_node_table_id,
        node.feishu_bitable_record_id,
        workflow_node_to_bitable_fields(node),
    )
