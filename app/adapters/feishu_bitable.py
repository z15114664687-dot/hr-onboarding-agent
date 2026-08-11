from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import date, datetime, time, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from app.adapters.feishu_auth import FEISHU_API_BASE, get_tenant_access_token
from app.adapters.feishu_http import request_with_retry
from app.core.config import CANDIDATE_FIELD_MAPPING, MATERIAL_FIELD_MAPPING, NODE_FIELD_MAPPING
from app.models.onboarding import CandidateCase, MaterialSubmission, WorkflowNode
from app.services.material_field_summary import material_summary_json, material_summary_text
from app.services.material_catalog import required_materials_for_employment_type
from app.services.material_standards import get_material_standard
from app.services.onboarding_workflow import NODE_DISPLAY_NAMES, NODE_GROUP_NAMES

MAX_BATCH_SIZE = 500
USER_FIELD_NAMES = {"HRBP", "直属经理", "负责人OpenID", "协同人OpenID"}


async def create_record(app_token: str, table_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Create a Feishu Bitable record."""

    app_token, table_id = normalize_bitable_ids(app_token, table_id)
    token = await get_tenant_access_token()
    async with httpx.AsyncClient(base_url=FEISHU_API_BASE, timeout=15) as client:
        response = await request_with_retry(
            client,
            "POST",
            f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records",
            headers={"Authorization": f"Bearer {token}"},
            json={"fields": _serialize_fields(fields)},
        )
        response.raise_for_status()
        return _feishu_json(response)


async def batch_create_records(app_token: str, table_id: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Create Feishu Bitable records in chunks of at most 500."""

    app_token, table_id = normalize_bitable_ids(app_token, table_id)
    results: list[dict[str, Any]] = []
    token = await get_tenant_access_token()
    async with httpx.AsyncClient(base_url=FEISHU_API_BASE, timeout=30) as client:
        for chunk in _chunks(records, MAX_BATCH_SIZE):
            response = await request_with_retry(
                client,
                "POST",
                f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create",
                headers={"Authorization": f"Bearer {token}"},
                json={"records": [{"fields": _serialize_fields(record)} for record in chunk]},
            )
            response.raise_for_status()
            results.append(_feishu_json(response))
    return results


async def update_record(app_token: str, table_id: str, record_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Update a Feishu Bitable record by record id."""

    app_token, table_id = normalize_bitable_ids(app_token, table_id)
    token = await get_tenant_access_token()
    async with httpx.AsyncClient(base_url=FEISHU_API_BASE, timeout=15) as client:
        response = await request_with_retry(
            client,
            "PUT",
            f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"fields": _serialize_fields(fields)},
        )
        response.raise_for_status()
        return _feishu_json(response)


async def delete_record(app_token: str, table_id: str, record_id: str) -> dict[str, Any]:
    """Delete a Feishu Bitable record by record id."""

    app_token, table_id = normalize_bitable_ids(app_token, table_id)
    token = await get_tenant_access_token()
    async with httpx.AsyncClient(base_url=FEISHU_API_BASE, timeout=15) as client:
        response = await request_with_retry(
            client,
            "DELETE",
            f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        return _feishu_json(response)


async def search_records(
    app_token: str,
    table_id: str,
    filter: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Search records in a Feishu Bitable table."""

    app_token, table_id = normalize_bitable_ids(app_token, table_id)
    token = await get_tenant_access_token()
    async with httpx.AsyncClient(base_url=FEISHU_API_BASE, timeout=15) as client:
        response = await request_with_retry(
            client,
            "POST",
            f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/search",
            headers={"Authorization": f"Bearer {token}"},
            json={"filter": filter or {}},
        )
        response.raise_for_status()
        return _feishu_json(response)


def candidate_case_to_bitable_fields(case: CandidateCase) -> dict[str, Any]:
    """Map local candidate case fields to Feishu Bitable field names."""

    current_node = _current_workflow_node(case)
    return _map_fields(
        CANDIDATE_FIELD_MAPPING,
        {
            "candidate_name": case.candidate_name,
            "mobile": case.mobile,
            "email": case.email,
            "feishu_open_id": case.feishu_open_id,
            "department": case.department,
            "position": case.position,
            "employment_type": case.employment_type,
            "job_level": case.job_level,
            "city": case.city,
            "hrbp_open_id": case.hrbp_open_id,
            "manager_open_id": case.manager_open_id,
            "expected_onboard_date": case.expected_onboard_date,
            "current_stage": _current_stage_label(case, current_node),
            "current_node": _node_display_label(current_node.node_name) if current_node else None,
            "next_due_date": current_node.due_date if current_node else None,
            "overdue_node_count": _overdue_node_count(case),
            "material_progress": _material_progress_label(case),
            "overall_status": _overall_status_label(case.overall_status),
            "risk_level": _risk_level_label(case.risk_level),
        },
    )


def workflow_node_to_bitable_fields(node: WorkflowNode) -> dict[str, Any]:
    """Map local workflow node fields to Feishu Bitable field names."""

    return _map_fields(
        NODE_FIELD_MAPPING,
        {
            "case_id": str(node.case_id),
            "node_group": _node_group_label(node.node_name),
            "node_code": node.node_name,
            "node_name": _node_display_label(node.node_name),
            "owner_open_id": node.owner_open_id,
            "collaborator_open_id": node.collaborator_open_id,
            "due_date": node.due_date,
            "completed_at": node.completed_at,
            "status": _node_status_label(node.status),
            "reminder_status": _reminder_status_label(node),
            "overdue_days": node.overdue_days,
            "reminder_count": _reminder_count(node),
            "escalation_level": "升级" if (node.escalation_level or 0) >= 2 else "普通",
        },
    )


def material_to_bitable_fields(material: MaterialSubmission) -> dict[str, Any]:
    """Map local material fields to Feishu Bitable field names."""

    return _map_fields(
        MATERIAL_FIELD_MAPPING,
        {
            "case_id": str(material.case_id),
            "candidate_name": material.case.candidate_name if material.case else None,
            "material_type": _material_display_label(material.material_type),
            "material_code": material.material_type,
            "employment_type": material.case.employment_type if material.case else None,
            "file_name": material.file_name,
            "file_token": material.file_token,
            "ocr_text": _material_ocr_summary(material),
            "extracted_fields_json": _material_extracted_summary_json(material),
            "review_status": _material_review_status_label(material.review_status),
            "submission_status": _material_submission_status_label(material.review_status),
            "review_reason": material.review_reason,
            "candidate_feedback": material.review_reason,
            "retry_count": material.retry_count,
            "submitted_at": material.created_at,
        },
    )


def normalize_bitable_ids(app_token: str, table_id: str) -> tuple[str, str]:
    """Accept either raw Bitable ids or copied Feishu Bitable/wiki URLs."""

    normalized_app_token = _clean_token(_extract_app_token(app_token) or app_token)
    normalized_table_id = _clean_token(_extract_table_id(table_id) or _extract_table_id(app_token) or table_id)
    return normalized_app_token, normalized_table_id


def _material_ocr_summary(material: MaterialSubmission) -> str:
    fields = _material_fields(material)
    return material_summary_text(
        material.material_type,
        fields,
        ocr_text=material.ocr_text or "",
        candidate_name=material.case.candidate_name if material.case else "",
    )


def _material_extracted_summary_json(material: MaterialSubmission) -> str:
    fields = _material_fields(material)
    return material_summary_json(
        material.material_type,
        fields,
        ocr_text=material.ocr_text or "",
        candidate_name=material.case.candidate_name if material.case else "",
    )


def _material_fields(material: MaterialSubmission) -> dict[str, Any]:
    try:
        value = json.loads(material.extracted_fields_json or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _extract_app_token(value: str) -> str | None:
    parsed = urlparse(value)
    if not parsed.scheme:
        return None
    path_parts = [part for part in parsed.path.split("/") if part]
    for marker in ("base", "wiki"):
        if marker in path_parts:
            index = path_parts.index(marker)
            if len(path_parts) > index + 1:
                return path_parts[index + 1]
    return None


def _extract_table_id(value: str) -> str | None:
    parsed = urlparse(value)
    query = parsed.query if parsed.scheme else value.lstrip("?")
    query_table = parse_qs(query).get("table")
    if query_table:
        return _clean_token(query_table[0])
    if not parsed.scheme:
        return None
    path_parts = [part for part in parsed.path.split("/") if part]
    if "tables" in path_parts:
        index = path_parts.index("tables")
        if len(path_parts) > index + 1:
            return _clean_token(path_parts[index + 1])
    return None


def _clean_token(value: str) -> str:
    return value.split("?", 1)[0].split("&", 1)[0].strip()


def _node_status_label(status: str) -> str:
    return {
        "not_started": "未开始",
        "pending": "进行中",
        "submitted": "已完成",
        "approved": "已完成",
        "rejected": "已阻塞",
        "overdue": "已阻塞",
        "skipped": "已完成",
    }.get(status, status)


def _current_workflow_node(case: CandidateCase) -> WorkflowNode | None:
    ordered_nodes = sorted(case.nodes, key=lambda item: (item.due_date, item.id or 0))
    return next((node for node in ordered_nodes if node.status not in {"submitted", "approved", "skipped"}), None)


def _current_stage_label(case: CandidateCase, current_node: WorkflowNode | None) -> str:
    if case.overall_status == "completed":
        return "已完成"
    if case.overall_status == "terminated":
        return "已终止"
    if not current_node:
        return "已完成"
    return _node_stage_label(current_node.node_name)


def _node_stage_label(node_name: str) -> str:
    if node_name in {"background_check", "medical_check"}:
        return "Offer后准备"
    if node_name == "document_submission":
        return "入职准备"
    if node_name in {
        "entry_preparation",
        "contract_signing",
        "compliance_commitment",
        "conflict_of_interest_declaration",
        "role_qualification_registration",
        "records_transfer",
    }:
        return "入职报到"
    if node_name.startswith("probation_"):
        return "试用期"
    if node_name == "conversion_review":
        return "转正考核"
    return NODE_GROUP_NAMES.get(node_name) or NODE_DISPLAY_NAMES.get(node_name, node_name)


def _node_group_label(node_name: str) -> str:
    return NODE_GROUP_NAMES.get(node_name) or _node_stage_label(node_name)


def _node_display_label(node_name: str) -> str:
    display_name = NODE_DISPLAY_NAMES.get(node_name, node_name)
    group_name = NODE_GROUP_NAMES.get(node_name)
    if group_name and display_name != group_name:
        return f"{group_name}-{display_name}"
    return display_name


def _overall_status_label(status: str) -> str:
    return {
        "pending": "待启动",
        "in_progress": "进行中",
        "risk": "有风险",
        "completed": "已完成",
        "terminated": "已终止",
    }.get(status, status)


def _risk_level_label(level: str) -> str:
    return {
        "low": "低",
        "medium": "中",
        "high": "高",
    }.get(level, level)


def _overdue_node_count(case: CandidateCase) -> int:
    return sum(1 for node in case.nodes if node.status == "overdue" or (node.overdue_days or 0) > 0)


def _material_progress_label(case: CandidateCase) -> str:
    required_items = [item for item in required_materials_for_employment_type(case.employment_type) if item.upload_required]
    latest = _latest_materials_by_type(case.materials)
    approved = sum(
        1
        for item in required_items
        if latest.get(item.code) and latest[item.code].review_status in {"auto_approved", "approved"}
    )
    pending = max(len(required_items) - len(latest), 0)
    needs_fix = sum(
        1
        for material in latest.values()
        if material.review_status in {"auto_rejected", "rejected", "manual_review"}
    )
    return f"{approved}/{len(required_items)} 已通过，{pending} 待提交，{needs_fix} 需处理"


def _latest_materials_by_type(materials: list[MaterialSubmission]) -> dict[str, MaterialSubmission]:
    latest: dict[str, MaterialSubmission] = {}
    for material in sorted(materials, key=lambda item: item.created_at or datetime.min):
        latest[material.material_type] = material
    return latest


def _reminder_status_label(node: WorkflowNode) -> str:
    if node.status in {"approved", "skipped"}:
        return "无需提醒"
    if (node.escalation_level or 0) >= 2:
        return "已升级"
    if node.status == "overdue" or (node.overdue_days or 0) > 0:
        return "超时已提醒"
    return "未提醒"


def _reminder_count(node: WorkflowNode) -> int:
    if (node.escalation_level or 0) >= 2:
        return 2
    if node.status == "overdue" or (node.overdue_days or 0) > 0:
        return 1
    return 0


def _material_display_label(material_type: str) -> str:
    standard = get_material_standard(material_type)
    if standard:
        return standard.display_name
    for employment_type in ("社招", "校招-境内", "校招-境外"):
        for item in required_materials_for_employment_type(employment_type):
            if item.code == material_type:
                return item.name
    return material_type


def _material_review_status_label(status: str) -> str:
    return {
        "pending": "待审核",
        "manual_review": "待审核",
        "auto_approved": "已通过",
        "approved": "已通过",
        "auto_rejected": "需补充",
        "rejected": "需补充",
    }.get(status, status)


def _material_submission_status_label(status: str) -> str:
    return {
        "pending": "已提交",
        "manual_review": "已提交",
        "auto_approved": "已通过",
        "approved": "已通过",
        "auto_rejected": "需补充",
        "rejected": "需补充",
    }.get(status, status)


def _feishu_json(response: httpx.Response) -> dict[str, Any]:
    data = response.json()
    if data.get("code", 0) != 0:
        raise RuntimeError(f"Feishu Bitable API error code={data.get('code')} msg={data.get('msg')}")
    return data


def _map_fields(mapping: dict[str, str], values: dict[str, Any]) -> dict[str, Any]:
    return {field_name: values[key] for key, field_name in mapping.items() if values.get(key) is not None}


def _serialize_fields(fields: dict[str, Any]) -> dict[str, Any]:
    serialized: dict[str, Any] = {}
    for key, value in fields.items():
        if key in USER_FIELD_NAMES:
            serialized[key] = _serialize_user_field(value)
        elif isinstance(value, datetime):
            serialized[key] = int(value.replace(tzinfo=value.tzinfo or timezone.utc).timestamp() * 1000)
        elif isinstance(value, date):
            serialized[key] = int(datetime.combine(value, time.min, tzinfo=timezone.utc).timestamp() * 1000)
        else:
            serialized[key] = value
    return serialized


def _serialize_user_field(value: Any) -> Any:
    if isinstance(value, str):
        return [{"id": value}]
    if isinstance(value, list):
        return [{"id": item} if isinstance(item, str) else item for item in value]
    return value


def _chunks(records: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for index in range(0, len(records), size):
        yield records[index : index + size]
