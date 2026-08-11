from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, selectinload

from app.models.onboarding import CandidateCase, WorkflowNode
from app.schemas.onboarding import CandidateCaseCreate, CaseProgress, WorkflowNodeRead
from app.services.workflow_config import WorkflowStep, get_active_workflow_steps
from app.utils.time_utils import add_business_days, add_months, days_overdue

logger = logging.getLogger(__name__)

ACTIVE_WORKFLOW: list[WorkflowStep] = list(get_active_workflow_steps())

NODE_DISPLAY_NAMES: dict[str, str] = {
    step.node_name: step.display_name for step in ACTIVE_WORKFLOW
}
NODE_GROUP_NAMES: dict[str, str] = {
    step.node_name: step.group_name for step in ACTIVE_WORKFLOW if step.group_name
}
NODE_STATUS_DISPLAY_NAMES: dict[str, str] = {
    "not_started": "未开始",
    "pending": "进行中",
    "submitted": "已确认",
    "approved": "已完成",
    "rejected": "已驳回",
    "overdue": "已超时",
    "skipped": "已跳过",
}
CANDIDATE_DONE_STATUSES = {"submitted", "approved", "skipped"}
CANDIDATE_HIDDEN_NODE_NAMES = {step.node_name for step in ACTIVE_WORKFLOW if not step.candidate_visible}
WORKFLOW_NODE_ORDER = {step.node_name: index for index, step in enumerate(ACTIVE_WORKFLOW)}


class DuplicateCandidateError(ValueError):
    """Raised when a new case matches an existing candidate."""


def create_candidate_case(db: Session, payload: CandidateCaseCreate) -> CandidateCase:
    """Create a candidate case and its configured onboarding workflow nodes."""

    duplicate = _find_duplicate_candidate_case(db, payload)
    if duplicate:
        case, reason = duplicate
        raise DuplicateCandidateError(
            f"疑似重复候选人：已存在 #{case.id} {case.candidate_name}（匹配{reason}）。请确认后再添加。"
        )

    workflow = list(get_active_workflow_steps())
    case = CandidateCase(
        candidate_name=payload.candidate_name,
        mobile=payload.mobile,
        email=str(payload.email) if payload.email else None,
        feishu_open_id=payload.feishu_open_id,
        feishu_union_id=payload.feishu_union_id,
        department=payload.department,
        position=payload.position,
        employment_type=payload.employment_type,
        job_level=payload.job_level,
        city=payload.city,
        hrbp_open_id=payload.hrbp_open_id,
        manager_open_id=payload.manager_open_id,
        expected_onboard_date=payload.expected_onboard_date,
        current_stage=workflow[0].node_name,
        overall_status="pending",
        risk_level="low",
    )
    for index, step in enumerate(workflow):
        owner_open_id = _resolve_owner_open_id(step, payload)
        collaborator_open_id = payload.manager_open_id if step.owner_role != "manager" else payload.hrbp_open_id
        case.nodes.append(
            WorkflowNode(
                node_name=step.node_name,
                owner_open_id=owner_open_id,
                collaborator_open_id=collaborator_open_id,
                due_date=_calculate_due_date(payload.expected_onboard_date, step),
                status="pending" if index == 0 else "not_started",
            )
        )
    db.add(case)
    db.commit()
    db.refresh(case)
    return case


def _find_duplicate_candidate_case(db: Session, payload: CandidateCaseCreate) -> tuple[CandidateCase, str] | None:
    open_id = _clean(payload.feishu_open_id)
    union_id = _clean(payload.feishu_union_id)
    mobile = _clean(payload.mobile)
    email = _clean(str(payload.email)) if payload.email else ""

    identity_checks = (
        ("候选人 open_id", CandidateCase.feishu_open_id == open_id if open_id else None),
        ("候选人 union_id", CandidateCase.feishu_union_id == union_id if union_id else None),
        ("手机号", CandidateCase.mobile == mobile if mobile else None),
        ("邮箱", func.lower(CandidateCase.email) == email.lower() if email else None),
    )
    for reason, criterion in identity_checks:
        if criterion is None:
            continue
        existing = db.query(CandidateCase).filter(criterion).order_by(CandidateCase.created_at.desc()).first()
        if existing:
            return existing, reason

    name = _clean(payload.candidate_name)
    department = _clean(payload.department)
    position = _clean(payload.position)
    existing = (
        db.query(CandidateCase)
        .filter(
            CandidateCase.candidate_name == name,
            CandidateCase.expected_onboard_date == payload.expected_onboard_date,
            CandidateCase.department == (department or None),
            CandidateCase.position == (position or None),
        )
        .order_by(CandidateCase.created_at.desc())
        .first()
    )
    if existing:
        return existing, "姓名、预计入职日期、条线和岗位"
    return None


def _clean(value: str | None) -> str:
    return str(value or "").strip()


def get_progress(db: Session, case_id: int) -> CaseProgress | None:
    """Return progress for a case id."""

    case = (
        db.query(CandidateCase)
        .options(selectinload(CandidateCase.nodes))
        .filter(CandidateCase.id == case_id)
        .one_or_none()
    )
    if not case:
        return None
    return _case_to_progress(case)


def get_progress_for_open_id(db: Session, open_id: str, union_id: str | None = None) -> str:
    """Return a human-readable progress summary for a Feishu open_id."""

    case = (
        db.query(CandidateCase)
        .options(selectinload(CandidateCase.nodes))
        .filter(_feishu_user_filter(open_id, union_id))
        .order_by(CandidateCase.created_at.desc())
        .first()
    )
    if not case:
        return "暂未找到你的入职记录，请联系 HR 确认飞书账号是否已绑定。"
    _refresh_open_id_if_needed(db, case, open_id)

    visible_nodes = candidate_visible_nodes(case.nodes)
    current_node = candidate_current_node(case, visible_nodes)
    lines = [
        f"{case.candidate_name} 的入职进度：",
        f"当前阶段：{_progress_node_label(current_node.node_name if current_node else case.current_stage)}",
        f"整体状态：{case.overall_status}，风险等级：{case.risk_level}",
    ]
    lines.extend(_progress_lines(visible_nodes))
    return "\n".join(lines)


def candidate_visible_nodes(nodes: list[WorkflowNode]) -> list[WorkflowNode]:
    """Hide HR-internal workflow checkpoints from candidate-facing surfaces."""

    return [node for node in nodes if node.node_name not in CANDIDATE_HIDDEN_NODE_NAMES]


def candidate_current_node(case: CandidateCase, nodes: list[WorkflowNode]) -> WorkflowNode | None:
    """Return the current candidate-visible node without mutating workflow state."""

    current = next((node for node in nodes if node.node_name == case.current_stage), None)
    if current:
        return current
    ordered_nodes = sorted(nodes, key=lambda item: (item.due_date, WORKFLOW_NODE_ORDER.get(item.node_name, 99)))
    return next((node for node in ordered_nodes if node.status not in CANDIDATE_DONE_STATUSES), None)


def _feishu_user_filter(open_id: str, union_id: str | None = None):
    if union_id:
        return or_(CandidateCase.feishu_open_id == open_id, CandidateCase.feishu_union_id == union_id)
    return CandidateCase.feishu_open_id == open_id


def _refresh_open_id_if_needed(db: Session, case: CandidateCase, open_id: str) -> None:
    if case.feishu_open_id == open_id:
        return
    case.feishu_open_id = open_id
    db.add(case)
    db.commit()
    db.refresh(case)


def mark_node_submitted(db: Session, case_id: int, node_name: str) -> WorkflowNode:
    """Mark a workflow node as submitted."""

    node = _get_node_or_raise(db, case_id, node_name)
    node.status = "submitted"
    node.overdue_days = 0
    node.escalation_level = 0
    _update_case_stage(db, node.case)
    db.commit()
    db.refresh(node)
    return node


def confirm_node_by_candidate(db: Session, case_id: int, node_name: str) -> WorkflowNode:
    """Record that the candidate confirmed a workflow node as completed/submitted."""

    node = _get_node_or_raise(db, case_id, node_name)
    if node.status not in {"approved", "skipped"}:
        node.status = "submitted"
        node.overdue_days = 0
        node.escalation_level = 0
    _update_case_stage(db, node.case)
    db.commit()
    db.refresh(node)
    return node


def approve_node(db: Session, case_id: int, node_name: str) -> WorkflowNode:
    """Approve a workflow node and advance the case stage."""

    node = _get_node_or_raise(db, case_id, node_name)
    node.status = "approved"
    node.completed_at = datetime.utcnow()
    _update_case_stage(db, node.case)
    db.commit()
    db.refresh(node)
    return node


def reject_node(db: Session, case_id: int, node_name: str, reason: str) -> WorkflowNode:
    """Reject a workflow node and mark the case as risk."""

    node = _get_node_or_raise(db, case_id, node_name)
    node.status = "rejected"
    node.case.overall_status = "risk"
    node.case.risk_level = "medium"
    logger.info("workflow_node_rejected case_id=%s node_name=%s", case_id, node_name)
    db.commit()
    db.refresh(node)
    return node


def scan_overdue_nodes(db: Session, today: date | None = None) -> list[WorkflowNode]:
    """Find overdue nodes, update overdue fields, and log future reminder intent."""

    overdue_nodes = (
        db.query(WorkflowNode)
        .filter(WorkflowNode.status.in_(["not_started", "pending", "overdue"]))
        .all()
    )
    changed: list[WorkflowNode] = []
    for node in overdue_nodes:
        overdue_days = days_overdue(node.due_date, today)
        if overdue_days <= 0:
            continue
        node.overdue_days = overdue_days
        node.status = "overdue"
        node.escalation_level = 2 if overdue_days >= 3 else 1
        node.case.overall_status = "risk"
        node.case.risk_level = "high" if overdue_days >= 3 else "medium"
        changed.append(node)
        logger.info(
            "workflow_node_overdue case_id=%s node_name=%s overdue_days=%s escalation_level=%s",
            node.case_id,
            node.node_name,
            overdue_days,
            node.escalation_level,
        )
    db.commit()
    return changed


def _get_node_or_raise(db: Session, case_id: int, node_name: str) -> WorkflowNode:
    node = (
        db.query(WorkflowNode)
        .options(selectinload(WorkflowNode.case).selectinload(CandidateCase.nodes))
        .filter(WorkflowNode.case_id == case_id, WorkflowNode.node_name == node_name)
        .one_or_none()
    )
    if not node:
        raise ValueError(f"workflow node not found: case_id={case_id}, node_name={node_name}")
    return node


def _update_case_stage(db: Session, case: CandidateCase) -> None:
    ordered_nodes = sorted(case.nodes, key=lambda item: item.due_date)
    next_node = next((node for node in ordered_nodes if node.status not in CANDIDATE_DONE_STATUSES), None)
    risk_nodes = [
        node
        for node in ordered_nodes
        if node.status in {"overdue", "rejected"} or (node.overdue_days or 0) > 0
    ]
    if not next_node:
        if ordered_nodes:
            case.current_stage = ordered_nodes[-1].node_name
        case.overall_status = "completed"
        case.risk_level = "low"
    else:
        case.current_stage = next_node.node_name
        if risk_nodes:
            case.overall_status = "risk"
            case.risk_level = "high" if any((node.escalation_level or 0) >= 2 or (node.overdue_days or 0) >= 3 for node in risk_nodes) else "medium"
        else:
            case.overall_status = "in_progress"
            case.risk_level = "low"
        if next_node.status == "not_started":
            next_node.status = "pending"
    db.add(case)


def _calculate_due_date(expected_onboard_date: date, step: WorkflowStep) -> date:
    if step.unit == "business_day":
        return add_business_days(expected_onboard_date, step.offset)
    if step.unit == "month":
        return add_months(expected_onboard_date, step.offset)
    return expected_onboard_date + timedelta(days=step.offset)


def _resolve_owner_open_id(step: WorkflowStep, payload: CandidateCaseCreate) -> str | None:
    if step.owner_role == "manager":
        return payload.manager_open_id or payload.hrbp_open_id
    return payload.hrbp_open_id


def _progress_node_label(node_name: str) -> str:
    display_name = NODE_DISPLAY_NAMES.get(node_name, node_name)
    group_name = NODE_GROUP_NAMES.get(node_name)
    if group_name and display_name != group_name:
        return f"{group_name}-{display_name}"
    return display_name


def _progress_lines(nodes: list[WorkflowNode]) -> list[str]:
    material_nodes = [node for node in nodes if NODE_GROUP_NAMES.get(node.node_name) == "材料递交"]
    material_node_ids = {node.id for node in material_nodes}
    lines: list[str] = []
    for node in nodes:
        if node.id in material_node_ids:
            if material_nodes and node is material_nodes[0]:
                lines.append("- 材料递交：")
                for child in material_nodes:
                    lines.append(f"  - {NODE_DISPLAY_NAMES.get(child.node_name, child.node_name)}：{_status_label(child.status)}，截止 {child.due_date.isoformat()}")
            continue
        lines.append(f"- {NODE_DISPLAY_NAMES.get(node.node_name, node.node_name)}：{_status_label(node.status)}，截止 {node.due_date.isoformat()}")
    return lines


def _status_label(status: str) -> str:
    return NODE_STATUS_DISPLAY_NAMES.get(status, status)


def _case_to_progress(case: CandidateCase) -> CaseProgress:
    return CaseProgress(
        case_id=case.id,
        candidate_name=case.candidate_name,
        current_stage=case.current_stage,
        overall_status=case.overall_status,
        risk_level=case.risk_level,
        nodes=[WorkflowNodeRead.model_validate(node) for node in case.nodes],
    )
