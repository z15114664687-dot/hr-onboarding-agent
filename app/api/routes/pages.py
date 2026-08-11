from __future__ import annotations

import base64
import binascii
import json
import re
import secrets
import time
from datetime import date, datetime
from html import escape
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.core.security import (
    attach_candidate_cookie,
    require_candidate_access,
    require_hr_access,
    require_same_origin,
)
from app.db.session import get_db
from app.models.onboarding import CandidateCase, MaterialSubmission, WorkflowNode
from app.services.material_catalog import (
    RequiredMaterial,
    normalize_employment_type,
    required_materials_for_employment_type,
)
from app.services.material_standards import get_material_standard
from app.services.material_submission_flow import (
    is_material_required_for_case,
    notify_candidate_material_batch_summary,
    notify_candidate_material_overall_summary,
    review_and_record_material_submission,
    sync_candidate_material_summary_to_bitable,
)
from app.services.bitable_sync import sync_case_to_bitable
from app.services.onboarding_workflow import (
    NODE_DISPLAY_NAMES,
    NODE_GROUP_NAMES,
    candidate_current_node,
    candidate_visible_nodes,
    confirm_node_by_candidate,
)
from app.services.org_catalog import BUSINESS_LINE_POSITIONS, DEPARTMENT_OPTIONS, EMPLOYMENT_TYPE_OPTIONS, POSITION_OPTIONS
from app.utils.time_utils import days_overdue

router = APIRouter(prefix="/ui", tags=["pages"])

MIME_BY_EXTENSION = {
    "pdf": {"application/pdf"},
    "png": {"image/png"},
    "jpg": {"image/jpeg"},
    "jpeg": {"image/jpeg"},
    "doc": {"application/msword", "application/octet-stream"},
    "docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/zip"},
}
DEFAULT_UPLOAD_ROOT = Path("uploads/materials")
UPLOAD_ROOT = DEFAULT_UPLOAD_ROOT

STATUS_LABELS = {
    "pending": "进行中",
    "in_progress": "进行中",
    "risk": "有风险",
    "completed": "已完成",
    "terminated": "已终止",
    "low": "低",
    "medium": "中",
    "high": "高",
    "not_started": "未开始",
    "submitted": "已确认",
    "approved": "已完成",
    "rejected": "已驳回",
    "overdue": "已超时",
    "skipped": "已跳过",
    "auto_approved": "自动通过",
    "auto_rejected": "自动驳回",
    "needs_supplement": "材料需补充",
    "manual_review": "人工复核",
    "not_submitted": "待提交",
}

MATERIAL_BUCKET_LABELS = {
    "campus_domestic": "校招-境内",
    "campus_overseas": "校招-境外",
    "social": "社招",
}

NODE_ORDER = [
    "background_check",
    "medical_check",
    "document_submission",
    "conflict_of_interest_declaration",
    "role_qualification_registration",
    "records_transfer",
    "entry_preparation",
    "contract_signing",
    "compliance_commitment",
    "probation_month_3",
    "probation_month_4",
    "probation_month_5",
    "conversion_review",
]

CASE_STATUS_OPTIONS = ("pending", "in_progress", "risk", "completed", "terminated")
RISK_LEVEL_OPTIONS = ("low", "medium", "high")
NODE_STATUS_OPTIONS = ("not_started", "pending", "submitted", "approved", "rejected", "overdue", "skipped")
IMPORT_TEMPLATE_PATH = Path("static/candidate_import_template.xlsx")


class MaterialUploadPayload(BaseModel):
    material_type: str
    file_name: str
    content_type: Optional[str] = None
    data_base64: str


class MaterialUploadFilePayload(BaseModel):
    file_name: str
    content_type: Optional[str] = None
    data_base64: str


class MaterialBatchUploadPayload(BaseModel):
    material_type: str
    files: list[MaterialUploadFilePayload]


class MaterialMultiBatchUploadItem(BaseModel):
    material_type: str
    files: list[MaterialUploadFilePayload]


class MaterialMultiBatchUploadPayload(BaseModel):
    items: list[MaterialMultiBatchUploadItem]


class HrCaseUpdatePayload(BaseModel):
    department: Optional[str] = None
    position: Optional[str] = None
    current_stage: str
    node_status: str
    overall_status: str
    risk_level: str


@router.get("", include_in_schema=False)
def ui_home() -> RedirectResponse:
    """Default Feishu web app homepage for HR users."""

    return RedirectResponse(url="/ui/hr/workspace")


@router.get("/hr", response_class=HTMLResponse, dependencies=[Depends(require_hr_access)])
def hr_dashboard(db: Session = Depends(get_db)) -> HTMLResponse:
    """Render the HR operations dashboard for local/Feishu webview demos."""

    cases = (
        db.query(CandidateCase)
        .options(selectinload(CandidateCase.nodes), selectinload(CandidateCase.materials))
        .order_by(CandidateCase.created_at.desc())
        .all()
    )
    overdue_nodes = (
        db.query(WorkflowNode)
        .options(selectinload(WorkflowNode.case))
        .filter(WorkflowNode.status == "overdue")
        .order_by(WorkflowNode.overdue_days.desc(), WorkflowNode.due_date.asc())
        .limit(12)
        .all()
    )
    materials = (
        db.query(MaterialSubmission)
        .options(selectinload(MaterialSubmission.case))
        .order_by(MaterialSubmission.created_at.desc())
        .limit(12)
        .all()
    )
    html = _page(
        title="HR 入职助理后台",
        body=f"""
        <section class="hero hr-hero">
          <div>
            <p class="eyebrow">HR Operations</p>
            <h1>入职流程与材料审核</h1>
            <p class="lead">多候选人流程跟踪、超时升级、材料审核结果集中查看。</p>
          </div>
          <div class="hero-metrics">
            {_metric("候选人", len(cases))}
            {_metric("风险中", sum(1 for case in cases if case.overall_status == "risk"))}
            {_metric("超时节点", sum(1 for case in cases for node in case.nodes if node.status == "overdue"))}
            {_metric("待人工", sum(1 for case in cases for material in case.materials if material.review_status == "manual_review"))}
          </div>
        </section>
        <main class="layout">
          <section class="panel wide">
            <div class="panel-head">
              <h2>候选人总览</h2>
              <span>{len(cases)} 人</span>
            </div>
            {_candidate_table(cases)}
          </section>
          <section class="panel">
            <div class="panel-head">
              <h2>超时催办</h2>
              <span>每日 09:00 自动扫描</span>
            </div>
            {_overdue_list(overdue_nodes)}
          </section>
          <section class="panel">
            <div class="panel-head">
              <h2>材料审核</h2>
              <span>最近 12 条</span>
            </div>
            {_material_list(materials)}
          </section>
        </main>
        """,
    )
    return HTMLResponse(html)


@router.get("/hr/workspace", response_class=HTMLResponse, dependencies=[Depends(require_hr_access)])
def hr_workspace_dashboard(db: Session = Depends(get_db)) -> HTMLResponse:
    """Render a separate HR workspace preview with line-position linked controls."""

    cases = (
        db.query(CandidateCase)
        .options(selectinload(CandidateCase.nodes), selectinload(CandidateCase.materials))
        .order_by(CandidateCase.created_at.desc())
        .all()
    )
    overdue_nodes = (
        db.query(WorkflowNode)
        .options(selectinload(WorkflowNode.case))
        .filter(WorkflowNode.status == "overdue")
        .order_by(WorkflowNode.overdue_days.desc(), WorkflowNode.due_date.asc())
        .limit(8)
        .all()
    )
    materials = (
        db.query(MaterialSubmission)
        .options(selectinload(MaterialSubmission.case))
        .order_by(MaterialSubmission.created_at.desc())
        .limit(8)
        .all()
    )
    html = _page(
        title="HR 入职协同工作台",
        body=f"""
        <section class="hr-workspace-hero">
          <div>
            <h1>HR 入职协同工作台</h1>
            <p>按业务条线维护候选人归属，岗位板块会随条线联动，并继续保存到后台和飞书多维表。</p>
          </div>
          <div class="hr-workspace-metrics">
            {_metric("候选人", len(cases))}
            {_metric("风险中", sum(1 for case in cases if case.overall_status == "risk"))}
            {_metric("超时节点", sum(1 for case in cases for node in case.nodes if node.status == "overdue"))}
            {_metric("材料待处理", sum(1 for case in cases for material in case.materials if material.review_status in {"manual_review", "auto_rejected", "rejected"}))}
          </div>
        </section>
        <main class="hr-workspace-layout">
          <section class="panel wide hr-workspace-table-panel">
            <div class="panel-head">
              <div>
                <h2>候选人总览</h2>
                <p class="panel-subtitle">条线、岗位、当前节点、状态和风险改完后，点保存再同步到飞书多维表。</p>
              </div>
              <div class="panel-actions">
                <span>{len(cases)} 人</span>
                <button type="button" class="secondary-action" data-toggle-create-case>新增候选人</button>
                <button type="button" class="secondary-action" data-toggle-import-cases>批量导入</button>
              </div>
            </div>
            {_candidate_create_form()}
            {_candidate_import_form()}
            {_candidate_line_table(cases)}
          </section>
          <section class="panel">
            <div class="panel-head">
              <h2>超时催办</h2>
              <span>最近 8 条</span>
            </div>
            {_overdue_list(overdue_nodes)}
          </section>
          <section class="panel">
            <div class="panel-head">
              <h2>材料审核</h2>
              <span>最近 8 条</span>
            </div>
            {_material_list(materials)}
          </section>
        </main>
        """,
    )
    return HTMLResponse(html)


@router.get("/hr/import-template", dependencies=[Depends(require_hr_access)])
def download_candidate_import_template() -> FileResponse:
    """Download the fixed-column candidate import template."""

    if not IMPORT_TEMPLATE_PATH.exists():
        raise HTTPException(status_code=404, detail="candidate import template not found")
    return FileResponse(
        IMPORT_TEMPLATE_PATH,
        filename="候选人批量导入模板.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.post(
    "/hr/cases/{case_id}/status",
    response_class=JSONResponse,
    dependencies=[Depends(require_hr_access)],
)
async def update_hr_case_status(
    case_id: int,
    payload: HrCaseUpdatePayload,
    request: Request,
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Update HR-facing status controls and sync the candidate workflow to Bitable."""

    require_same_origin(request)
    case = (
        db.query(CandidateCase)
        .options(selectinload(CandidateCase.nodes), selectinload(CandidateCase.materials))
        .filter(CandidateCase.id == case_id)
        .one_or_none()
    )
    if not case:
        raise HTTPException(status_code=404, detail="case not found")
    if payload.overall_status not in CASE_STATUS_OPTIONS:
        raise HTTPException(status_code=400, detail="invalid overall_status")
    if payload.risk_level not in RISK_LEVEL_OPTIONS:
        raise HTTPException(status_code=400, detail="invalid risk_level")
    if payload.node_status not in NODE_STATUS_OPTIONS:
        raise HTTPException(status_code=400, detail="invalid node_status")

    node = _find_case_node(case, payload.current_stage)
    if not node:
        raise HTTPException(status_code=400, detail="invalid current_stage")
    _apply_node_status(node, payload.node_status)
    if payload.department is not None:
        case.department = payload.department.strip()
    if payload.position is not None:
        case.position = payload.position.strip()
    case.current_stage = payload.current_stage
    case.overall_status = payload.overall_status
    case.risk_level = payload.risk_level
    db.add(case)
    db.commit()
    db.refresh(case)

    sync_result = await sync_case_to_bitable(case, db)
    return JSONResponse(
        {
            "status": "success",
            "message": "已保存，并已尝试同步飞书多维表格。",
            "overall_status": case.overall_status,
            "overall_status_label": _label(case.overall_status),
            "risk_level": case.risk_level,
            "risk_level_label": _label(case.risk_level),
            "department": case.department,
            "position": case.position,
            "current_stage": case.current_stage,
            "current_node_label": _node_label(case.current_stage),
            "node_status": node.status,
            "node_status_label": _label(node.status),
            "bitable_sync": sync_result,
        }
    )


@router.get("/candidate/{case_id}", response_class=HTMLResponse)
def candidate_page(case_id: int, request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """Render a candidate-facing progress and material page."""

    query_token = require_candidate_access(request, case_id)
    case = (
        db.query(CandidateCase)
        .options(selectinload(CandidateCase.nodes), selectinload(CandidateCase.materials))
        .filter(CandidateCase.id == case_id)
        .one_or_none()
    )
    if not case:
        raise HTTPException(status_code=404, detail="case not found")

    visible_nodes = candidate_visible_nodes(case.nodes)
    current_node = candidate_current_node(case, visible_nodes)
    html = _page(
        title=f"{case.candidate_name} 的入职进度",
        body=f"""
        <section class="hero candidate-hero">
          <div>
            <p class="eyebrow">Candidate View</p>
            <h1>{escape(case.candidate_name)} 的入职进度</h1>
            <p class="lead">预计入职：{_date(case.expected_onboard_date)} · 当前阶段：{_node_label(current_node.node_name if current_node else case.current_stage)}</p>
          </div>
          <div class="hero-metrics">
            {_metric("整体状态", _label(case.overall_status))}
            {_metric("风险等级", _label(case.risk_level))}
            {_metric("待完成", sum(1 for node in visible_nodes if node.status not in {"submitted", "approved", "skipped"}))}
          </div>
        </section>
        <div class="notice-list" aria-live="polite"></div>
        <main class="candidate-layout">
          <section class="panel flow-panel">
            <div class="panel-head">
              <h2>流程重点</h2>
              <span>按时间排序</span>
            </div>
            {_timeline(visible_nodes)}
          </section>
          <section class="panel material-status-panel collapsible-panel" id="materials">
            <div class="panel-head">
              <div>
                <h2>材料状态</h2>
                <p class="panel-subtitle">最新提交在最上方</p>
              </div>
              <div class="submitted-actions">
                <span class="submitted-count">{len(case.materials)} 条</span>
                <button type="button" class="history-toggle panel-toggle" aria-expanded="false">展开</button>
                <a class="action-link" href="/ui/candidate/{case.id}/materials">提交材料</a>
              </div>
            </div>
            <div class="candidate-material-list collapsible-list is-collapsed">
              {_material_list(case.materials, compact=False)}
            </div>
          </section>
        </main>
        """,
    )
    response = HTMLResponse(html)
    attach_candidate_cookie(response, case_id, query_token)
    return response


@router.get("/candidate/{case_id}/workspace", response_class=HTMLResponse)
def candidate_workspace_page(
    case_id: int,
    request: Request,
    uploaded: Optional[str] = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Render an alternative one-page candidate workspace for progress and materials."""

    query_token = require_candidate_access(request, case_id)
    case = (
        db.query(CandidateCase)
        .options(selectinload(CandidateCase.nodes), selectinload(CandidateCase.materials))
        .filter(CandidateCase.id == case_id)
        .one_or_none()
    )
    if not case:
        raise HTTPException(status_code=404, detail="case not found")

    bucket = normalize_employment_type(case.employment_type)
    materials = required_materials_for_employment_type(case.employment_type)
    upload_items = [item for item in materials if item.upload_required and is_material_required_for_case(case, item.code)]
    latest = _latest_materials_by_type(case.materials)
    approved_count = sum(
        1 for item in upload_items if _material_status_key(latest.get(item.code)) in {"approved", "auto_approved"}
    )
    needs_fix_count = sum(
        1
        for item in upload_items
        if _material_status_key(latest.get(item.code)) in {"rejected", "auto_rejected", "manual_review", "needs_supplement"}
    )
    not_submitted_count = sum(1 for item in upload_items if item.code not in latest)
    visible_nodes = candidate_visible_nodes(case.nodes)
    completed_nodes = sum(1 for node in visible_nodes if node.status in {"submitted", "approved", "skipped"})
    current_node = candidate_current_node(case, visible_nodes)

    html = _page(
        title=f"{case.candidate_name} 的入职任务中心",
        body=f"""
        <section class="workspace-hero">
          <div class="workspace-title">
            <div class="workspace-kicker">入职任务中心</div>
            <h1>{escape(case.candidate_name)} 的入职进度与材料</h1>
            <p>预计入职：{_date(case.expected_onboard_date)} · 当前阶段：{_node_label(current_node.node_name if current_node else case.current_stage)} · 材料口径：{MATERIAL_BUCKET_LABELS[bucket]}</p>
          </div>
          <div class="workspace-status-strip" aria-label="当前入职状态">
            {_workspace_stat("整体状态", _label(case.overall_status), case.overall_status)}
            {_workspace_stat("风险等级", _label(case.risk_level), case.risk_level)}
            {_workspace_stat("流程节点", f"{completed_nodes}/{len(visible_nodes)}", "submitted")}
            {_workspace_stat("材料", f"{approved_count}/{len(upload_items)}", "approved")}
          </div>
        </section>
        <div class="notice-list" aria-live="polite">
          {_upload_flash(uploaded, latest) if uploaded else ""}
        </div>
        <main class="workspace-layout">
          <section class="workspace-column">
            <section class="panel workspace-panel workspace-next">
              <div class="panel-head tall">
                <div>
                  <h2>当前要处理</h2>
                  <p class="panel-subtitle">优先处理超时节点、当前节点和需要补交的材料。</p>
                </div>
                <span>{_date(current_node.due_date if current_node else None)}</span>
              </div>
              {_workspace_next_actions(visible_nodes, current_node, upload_items, latest)}
            </section>
            <section class="panel workspace-panel workspace-flow">
              <div class="panel-head">
                <h2>流程时间线</h2>
                <span>{completed_nodes}/{len(visible_nodes)} 已确认</span>
              </div>
              {_timeline(visible_nodes)}
            </section>
            <section class="panel workspace-panel submitted-records workspace-submitted-records">
              <div class="panel-head">
                <div>
                  <h2>已提交记录</h2>
                  <p class="panel-subtitle">最新提交在最上方</p>
                </div>
                <span class="submitted-count">{len(case.materials)} 条</span>
              </div>
              <div class="submitted-records-list">
                {_material_list(case.materials, compact=False)}
              </div>
            </section>
          </section>
          <section class="workspace-column">
            <section class="panel workspace-panel workspace-materials" id="materials">
              <div class="panel-head tall">
                <div>
                  <h2>材料提交与审核反馈</h2>
                  <p class="panel-subtitle">材料会在本页上传并审核，最新反馈会显示在对应材料下方。</p>
                </div>
                <div class="workspace-material-summary">
                  {_workspace_stat("待提交", not_submitted_count, "not_submitted")}
                  {_workspace_stat("需处理", needs_fix_count, "risk")}
                </div>
              </div>
              {_workspace_progress_bar(approved_count, len(upload_items))}
              {_bulk_submit_panel()}
              {_material_checklist(upload_items, latest)}
            </section>
          </section>
        </main>
        """,
    )
    response = HTMLResponse(html)
    attach_candidate_cookie(response, case_id, query_token)
    return response


@router.post("/candidate/{case_id}/nodes/{node_name}/confirm", response_class=JSONResponse)
async def confirm_candidate_node(
    case_id: int,
    node_name: str,
    request: Request,
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Let a candidate mark a workflow node as confirmed from the H5 page."""

    require_candidate_access(request, case_id)
    require_same_origin(request)
    try:
        node = confirm_node_by_candidate(db, case_id, node_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="node not found") from exc
    case = (
        db.query(CandidateCase)
        .options(selectinload(CandidateCase.nodes), selectinload(CandidateCase.materials))
        .filter(CandidateCase.id == case_id)
        .one_or_none()
    )
    if not case:
        raise HTTPException(status_code=404, detail="case not found")

    sync_result = await sync_case_to_bitable(case, db)
    return JSONResponse(
        {
            "status": "success",
            "message": f"{_node_label(node.node_name)}已确认，系统会通知复核。",
            "node_name": node.node_name,
            "node_label": _node_label(node.node_name),
            "node_status": node.status,
            "node_status_label": _label(node.status),
            "overall_status": case.overall_status,
            "overall_status_label": _label(case.overall_status),
            "risk_level": case.risk_level,
            "risk_level_label": _label(case.risk_level),
            "bitable_sync": sync_result,
        }
    )


@router.get("/candidate/{case_id}/materials", response_class=HTMLResponse)
def candidate_materials_page(
    case_id: int,
    request: Request,
    uploaded: Optional[str] = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Render the candidate-facing material submission checklist."""

    query_token = require_candidate_access(request, case_id)
    case = (
        db.query(CandidateCase)
        .options(selectinload(CandidateCase.materials))
        .filter(CandidateCase.id == case_id)
        .one_or_none()
    )
    if not case:
        raise HTTPException(status_code=404, detail="case not found")

    bucket = normalize_employment_type(case.employment_type)
    materials = required_materials_for_employment_type(case.employment_type)
    upload_items = [item for item in materials if item.upload_required and is_material_required_for_case(case, item.code)]
    follow_up_items = [item for item in materials if not item.upload_required]
    latest = _latest_materials_by_type(case.materials)
    approved_count = sum(
        1 for item in upload_items if _material_status_key(latest.get(item.code)) in {"approved", "auto_approved"}
    )
    needs_fix_count = sum(
        1
        for item in upload_items
        if _material_status_key(latest.get(item.code)) in {"rejected", "auto_rejected", "manual_review"}
    )
    not_submitted_count = sum(1 for item in upload_items if item.code not in latest)

    html = _page(
        title=f"{case.candidate_name} 的材料提交",
        body=f"""
        <section class="hero materials-hero">
          <div>
            <p class="eyebrow">Materials</p>
            <h1>{escape(case.candidate_name)} 的材料提交</h1>
            <p class="lead">已按“{MATERIAL_BUCKET_LABELS[bucket]}”口径生成材料清单。预计入职：{_date(case.expected_onboard_date)}</p>
          </div>
          <div class="hero-metrics">
            {_metric("需上传", len(upload_items), key="required")}
            {_metric("待提交", not_submitted_count, key="not_submitted")}
            {_metric("已通过", approved_count, key="approved")}
            {_metric("需处理", needs_fix_count, key="needs_fix")}
          </div>
        </section>
        <div class="notice-list" aria-live="polite">
          {_upload_flash(uploaded, latest) if uploaded else ""}
        </div>
        <main class="materials-layout">
          <section class="panel materials-primary">
            <div class="panel-head tall">
              <div>
                <h2>需上传至预入职系统</h2>
                <p class="panel-subtitle">提交前请点击保存；全部材料确认无误后再提交审核。</p>
              </div>
              <a class="action-link" href="/ui/candidate/{case.id}">查看流程</a>
            </div>
            {_bulk_submit_panel()}
            {_material_checklist(upload_items, latest)}
          </section>
          <aside class="side-stack">
            <section class="panel">
              <div class="panel-head">
                <h2>入职后关注事项</h2>
                <span>{len(follow_up_items)} 项</span>
              </div>
              {_follow_up_list(follow_up_items)}
            </section>
            <section class="panel submitted-records">
              <div class="panel-head">
                <div>
                  <h2>已提交记录</h2>
                  <p class="panel-subtitle">最新提交在最上方</p>
                </div>
                <div class="submitted-actions">
                  <span class="submitted-count">{len(case.materials)} 条</span>
                  <button type="button" class="history-toggle" aria-expanded="false">展开</button>
                </div>
              </div>
              <div class="submitted-records-list is-collapsed">
                {_material_list(case.materials, compact=False)}
              </div>
            </section>
          </aside>
        </main>
        """,
    )
    response = HTMLResponse(html)
    attach_candidate_cookie(response, case_id, query_token)
    return response


@router.post("/candidate/{case_id}/workspace/upload", response_class=JSONResponse)
@router.post("/candidate/{case_id}/materials/upload", response_class=JSONResponse)
async def upload_candidate_material(
    case_id: int,
    payload: MaterialUploadPayload,
    request: Request,
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Accept a candidate material upload from the H5 page and run review/sync/notify."""

    require_candidate_access(request, case_id)
    require_same_origin(request)
    case = _material_upload_case(db, case_id)
    _validate_material_required_for_upload(case, payload.material_type)
    result = await _record_material_upload_file(
        db,
        case,
        material_type=payload.material_type,
        file_name=payload.file_name,
        content_type=payload.content_type,
        data_base64=payload.data_base64,
        notify_candidate=True,
    )
    return JSONResponse(_public_upload_result(result))


@router.post("/candidate/{case_id}/workspace/batch-upload", response_class=JSONResponse)
@router.post("/candidate/{case_id}/materials/batch-upload", response_class=JSONResponse)
async def upload_candidate_material_batch(
    case_id: int,
    payload: MaterialBatchUploadPayload,
    request: Request,
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Accept multiple files for one material item and send one candidate summary notification."""

    require_candidate_access(request, case_id)
    require_same_origin(request)
    case = _material_upload_case(db, case_id)
    _validate_material_required_for_upload(case, payload.material_type)
    if not payload.files:
        raise HTTPException(status_code=400, detail="please upload at least one file")

    results: list[dict[str, Any]] = []
    raw_results: list[dict[str, Any]] = []
    for file_payload in payload.files:
        item = await _record_material_upload_file(
            db,
            case,
            material_type=payload.material_type,
            file_name=file_payload.file_name,
            content_type=file_payload.content_type,
            data_base64=file_payload.data_base64,
            notify_candidate=False,
            sync_candidate=False,
        )
        results.append(item)
        raw_results.append(item["_raw_result"])
    candidate_sync = await sync_candidate_material_summary_to_bitable(case)
    candidate_notify = await notify_candidate_material_batch_summary(
        case,
        material_type=payload.material_type,
        results=raw_results,
    )
    summary = _batch_upload_summary(payload.material_type, results)
    return JSONResponse(
        {
            "status": "success",
            "material_type": payload.material_type,
            "material_name": _material_name(payload.material_type),
            "review_status": summary["review_status"],
            "review_label": _label(summary["review_status"]),
            "message": summary["message"],
            "file_name": f"{len(results)} 个文件",
            "review_reason": summary["message"],
            "results": [_public_upload_result(item) for item in results],
            "candidate_sync": candidate_sync,
            "candidate_notify": candidate_notify,
        }
    )


@router.post("/candidate/{case_id}/workspace/batch-submit", response_class=JSONResponse)
@router.post("/candidate/{case_id}/materials/batch-submit", response_class=JSONResponse)
async def submit_candidate_materials_batch(
    case_id: int,
    payload: MaterialMultiBatchUploadPayload,
    request: Request,
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Accept files across multiple material items and send one candidate summary notification."""

    require_candidate_access(request, case_id)
    require_same_origin(request)
    case = _material_upload_case(db, case_id)
    if not payload.items:
        raise HTTPException(status_code=400, detail="please upload at least one material item")

    public_groups: list[dict[str, Any]] = []
    raw_groups: list[dict[str, Any]] = []
    for item_payload in payload.items:
        _validate_material_required_for_upload(case, item_payload.material_type)
        if not item_payload.files:
            raise HTTPException(status_code=400, detail=f"{item_payload.material_type} has no files")
        results: list[dict[str, Any]] = []
        raw_results: list[dict[str, Any]] = []
        for file_payload in item_payload.files:
            item = await _record_material_upload_file(
                db,
                case,
                material_type=item_payload.material_type,
                file_name=file_payload.file_name,
                content_type=file_payload.content_type,
                data_base64=file_payload.data_base64,
                notify_candidate=False,
                sync_candidate=False,
            )
            results.append(item)
            raw_results.append(item["_raw_result"])
        group_summary = _batch_upload_summary(item_payload.material_type, results)
        public_groups.append(
            {
                "material_type": item_payload.material_type,
                "material_name": _material_name(item_payload.material_type),
                "review_status": group_summary["review_status"],
                "review_label": _label(group_summary["review_status"]),
                "message": group_summary["message"],
                "file_name": f"{len(results)} 个文件",
                "review_reason": group_summary["message"],
                "results": [_public_upload_result(item) for item in results],
            }
        )
        raw_groups.append(
            {
                "material_type": item_payload.material_type,
                "material_name": _material_name(item_payload.material_type),
                "results": raw_results,
            }
        )

    candidate_sync = await sync_candidate_material_summary_to_bitable(case)
    candidate_notify = await notify_candidate_material_overall_summary(case, groups=raw_groups)
    summary = _multi_batch_upload_summary(public_groups)
    return JSONResponse(
        {
            "status": "success",
            "summary_title": "材料统一提交",
            "material_count": len(public_groups),
            "file_count": sum(len(group["results"]) for group in public_groups),
            "review_status": summary["review_status"],
            "review_label": _label(summary["review_status"]),
            "message": summary["message"],
            "review_reason": summary["message"],
            "groups": public_groups,
            "candidate_sync": candidate_sync,
            "candidate_notify": candidate_notify,
        }
    )


def _material_upload_case(db: Session, case_id: int) -> CandidateCase:
    case = (
        db.query(CandidateCase)
        .options(selectinload(CandidateCase.materials))
        .filter(CandidateCase.id == case_id)
        .one_or_none()
    )
    if not case:
        raise HTTPException(status_code=404, detail="case not found")
    return case


def _validate_material_required_for_upload(case: CandidateCase, material_type: str) -> None:
    allowed_materials = {
        item.code for item in required_materials_for_employment_type(case.employment_type) if item.upload_required
    }
    if material_type not in allowed_materials:
        raise HTTPException(status_code=400, detail="material type is not required for this candidate")


async def _record_material_upload_file(
    db: Session,
    case: CandidateCase,
    *,
    material_type: str,
    file_name: str,
    content_type: str | None,
    data_base64: str,
    notify_candidate: bool,
    sync_candidate: bool = True,
) -> dict[str, Any]:
    file_bytes = _decode_upload_data(data_base64)
    safe_name = _safe_file_name(file_name)
    _validate_upload_content(material_type, safe_name, content_type, file_bytes)
    destination = _upload_path(case.id, material_type, safe_name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(file_bytes)

    result = await review_and_record_material_submission(
        db=db,
        case=case,
        material_type=material_type,
        file_path=str(destination),
        file_name=safe_name,
        notify_candidate=notify_candidate,
        sync_candidate=sync_candidate,
    )
    review = result["review"]
    material = result["material"]
    reason = (material.review_reason or "").strip()
    display_status = _material_display_status(review.decision, reason)
    return {
        "status": "success",
        "material_type": material_type,
        "material_name": _material_name(material_type),
        "review_status": display_status,
        "review_label": _label(display_status),
        "message": review.message_to_candidate,
        "file_name": safe_name,
        "review_reason": reason,
        "record_html": _material_item(material, compact=False),
        "redirect_url": f"/ui/candidate/{case.id}/materials?uploaded={material_type}",
        "bitable_sync": result["bitable_sync"],
        "candidate_sync": result["candidate_sync"],
        "candidate_notify": result["candidate_notify"],
        "_raw_result": result,
    }


def _public_upload_result(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if not key.startswith("_")}


def _batch_upload_summary(material_type: str, results: list[dict[str, Any]]) -> dict[str, str]:
    approved_count = sum(1 for item in results if _batch_file_passed(item))
    manual_count = sum(1 for item in results if item.get("review_status") == "manual_review")
    rejected_count = sum(1 for item in results if item.get("review_status") in {"auto_rejected", "rejected"})
    missing_items = _batch_missing_items(results)
    if missing_items:
        review_status = "needs_supplement"
    elif rejected_count:
        review_status = "auto_rejected"
    elif manual_count:
        review_status = "manual_review"
    else:
        review_status = "auto_approved"
    message = (
        f"本次{_material_name(material_type)}共提交 {len(results)} 个文件："
        f"文件已通过 {approved_count} 个，仍需补充 {len(missing_items)} 项，"
        f"未通过 {rejected_count} 个，人工复核 {manual_count} 个。"
    )
    if missing_items:
        message += f" 仍需补充：{'、'.join(missing_items[:8])}。"
    return {"review_status": review_status, "message": message}


def _multi_batch_upload_summary(groups: list[dict[str, Any]]) -> dict[str, str]:
    results = [item for group in groups for item in group.get("results", [])]
    approved_count = sum(1 for item in results if _batch_file_passed(item))
    manual_count = sum(1 for item in results if item.get("review_status") == "manual_review")
    rejected_count = sum(1 for item in results if item.get("review_status") in {"auto_rejected", "rejected"})
    missing_items = _multi_batch_missing_items(groups)
    if missing_items:
        review_status = "needs_supplement"
    elif rejected_count:
        review_status = "auto_rejected"
    elif manual_count:
        review_status = "manual_review"
    else:
        review_status = "auto_approved"
    message = (
        f"本次共提交 {len(groups)} 个材料项、{len(results)} 个文件："
        f"文件已通过 {approved_count} 个，仍需补充 {len(missing_items)} 项，"
        f"已驳回 {rejected_count} 个，需复核 {manual_count} 个。"
    )
    if missing_items:
        message += f" 仍需补充：{'、'.join(missing_items[:8])}。"
    return {"review_status": review_status, "message": message}


def _batch_file_passed(item: dict[str, Any]) -> bool:
    status = str(item.get("review_status") or "")
    reason = str(item.get("review_reason") or item.get("message") or "")
    return status in {"approved", "auto_approved"} or (status == "needs_supplement" and _is_supplement_reason(reason))


def _batch_missing_items(results: list[dict[str, Any]]) -> list[str]:
    missing: list[str] = []
    for item in results:
        for value in _supplement_items_from_reason(str(item.get("review_reason") or item.get("message") or "")):
            if value not in missing:
                missing.append(value)
    return missing


def _multi_batch_missing_items(groups: list[dict[str, Any]]) -> list[str]:
    missing: list[str] = []
    for group in groups:
        material_name = str(group.get("material_name") or group.get("material_type") or "材料")
        for item in _batch_missing_items(group.get("results", [])):
            value = f"{material_name}：{item}"
            if value not in missing:
                missing.append(value)
    return missing


def _supplement_items_from_reason(reason: str) -> list[str]:
    items: list[str] = []
    match = re.search(r"还缺[:：]\s*([^。；;]+)", reason)
    if match:
        items.extend(part.strip() for part in re.split(r"[、,，]", match.group(1)) if part.strip())
    if not items and "请先上传求职简历" in reason:
        items.append("求职简历")
    return items


def _workspace_stat(label: str, value: object, tone: str) -> str:
    return f"""
    <div class="workspace-stat {escape(tone.replace("_", "-"))}">
      <span>{escape(label)}</span>
      <strong>{escape(str(value))}</strong>
    </div>
    """


def _workspace_progress_bar(done: int, total: int) -> str:
    percent = 0 if total <= 0 else max(0, min(100, round(done / total * 100)))
    return f"""
    <div class="workspace-progress" aria-label="材料通过进度">
      <div>
        <strong>{done}/{total}</strong>
        <span>材料已通过</span>
      </div>
      <div class="workspace-progress-track" style="--progress: {percent}%">
        <span></span>
      </div>
    </div>
    """


def _workspace_next_actions(
    nodes: list[WorkflowNode],
    current_node: WorkflowNode | None,
    upload_items: list[RequiredMaterial],
    latest: dict[str, MaterialSubmission],
) -> str:
    actions: list[str] = []
    overdue_nodes = sorted(
        [node for node in nodes if node.status == "overdue"],
        key=lambda node: (-node.overdue_days, node.due_date),
    )
    for node in overdue_nodes[:2]:
        actions.append(
            _workspace_action(
                title=f"{_node_label(node.node_name)}已超时",
                meta=f"原截止 {_date(node.due_date)} · 已超时 {node.overdue_days} 天",
                href=f"#node-{node.node_name}",
                action="查看节点",
                tone="overdue",
            )
        )
    if current_node and current_node.status not in {"submitted", "approved", "skipped"}:
        actions.append(
            _workspace_action(
                title=f"当前节点：{_node_label(current_node.node_name)}",
                meta=f"截止 {_date(current_node.due_date)} · {_label(current_node.status)}",
                href=f"#node-{current_node.node_name}",
                action="确认进度",
                tone=current_node.status,
            )
        )

    material_statuses = {"not_submitted", "needs_supplement", "auto_rejected", "rejected", "manual_review"}
    for item in upload_items:
        material = latest.get(item.code)
        status = _material_status_key(material)
        if status not in material_statuses:
            continue
        actions.append(
            _workspace_action(
                title=f"{item.name}：{_label(status)}",
                meta=item.description,
                href=f"#material-{item.code}",
                action="处理材料",
                tone=status,
            )
        )
        if len(actions) >= 5:
            break

    if not actions:
        return _empty("当前没有需要立即处理的节点或材料。")
    return '<div class="workspace-actions">' + "".join(actions[:5]) + "</div>"


def _workspace_action(title: str, meta: str, href: str, action: str, tone: str) -> str:
    return f"""
    <article class="workspace-action {escape(tone.replace("_", "-"))}">
      <div>
        <strong>{escape(title)}</strong>
        <p>{escape(meta)}</p>
      </div>
      <a href="{escape(href)}">{escape(action)}</a>
    </article>
    """


def _material_checklist(items: list[RequiredMaterial], latest: dict[str, MaterialSubmission]) -> str:
    rows = []
    for index, item in enumerate(items, start=1):
        submission = latest.get(item.code)
        standard = get_material_standard(item.code)
        status_key = _material_status_key(submission)
        format_text = " / ".join(standard.accepted_formats) if standard else "按 HR 要求"
        rules = list(standard.upload_rules[:3]) if standard else [item.description]
        if item.description not in rules:
            rules.insert(0, item.description)
        reason = escape((submission.review_reason or "").strip()) if submission and submission.review_reason else ""
        guidance_html = _material_guidance(status_key, reason) if submission else _material_rules(rules)
        upload_action = _material_upload_action(submission)
        accept_attr = _accept_attr(standard.accepted_formats if standard else ())
        rows.append(
            f"""
            <article class="material-card" id="material-{escape(item.code)}">
              <div class="material-top">
                <div class="material-title">
                  <span>{index:02d}</span>
                  <h3>{escape(item.name)}</h3>
                </div>
                {_status(status_key)}
              </div>
              <p class="material-desc">{escape(item.description)}</p>
              <div class="material-meta">
                <span>接受格式：{escape(format_text)}</span>
                <span>文件状态：{escape(submission.file_name if submission and submission.file_name else "未收到文件")}</span>
              </div>
              {guidance_html}
              <form class="upload-form" data-material-type="{escape(item.code)}">
                <label class="file-control">
                  <input type="file" name="file" required multiple {accept_attr}>
                  <span>选择文件</span>
                </label>
                <button type="button" class="queue-upload-button">加入待提交</button>
                <button type="submit">{upload_action}</button>
                <p class="upload-status" aria-live="polite"></p>
              </form>
            </article>
            """
        )
    return "<div class='material-grid'>" + "".join(rows) + "</div>"


def _bulk_submit_panel() -> str:
    return """
      <div class="bulk-submit-panel" data-bulk-submit-panel>
        <div class="bulk-submit-copy">
          <strong>统一提交</strong>
          <span data-bulk-submit-count>待提交 0 个材料项、0 个文件</span>
        </div>
        <div class="bulk-submit-list" aria-live="polite">可先在多个材料项中选择文件并加入待提交。</div>
        <div class="bulk-submit-actions">
          <button type="button" class="bulk-clear-button" data-clear-bulk-submit disabled>清空</button>
          <button type="button" class="bulk-submit-button" data-submit-all disabled>提交全部</button>
        </div>
      </div>
    """


def _material_rules(rules: list[str]) -> str:
    return "<ul class='rule-list'>" + "".join(f"<li>{escape(rule)}</li>" for rule in rules) + "</ul>"


def _material_guidance(status_key: str, reason: str) -> str:
    message = reason or escape(_material_result_message(status_key))
    return f"""
    <div class="material-result {escape(status_key.replace("_", "-"))}">
      <strong>{_label(status_key)}</strong>
      <p>{message}</p>
    </div>
    """


def _material_result_message(status_key: str) -> str:
    return {
        "auto_approved": "材料已通过审核，无需重复上传。",
        "approved": "材料已通过审核，无需重复上传。",
        "manual_review": "材料已收到，HR 将进行人工复核。",
        "needs_supplement": "材料已通过单份检查，请继续补充缺少的文件。",
        "auto_rejected": "材料未通过审核，请按反馈重新上传。",
        "rejected": "材料未通过审核，请按反馈重新上传。",
    }.get(status_key, "材料已提交，请等待审核。")


def _upload_flash(uploaded: str, latest: dict[str, MaterialSubmission]) -> str:
    material = latest.get(uploaded)
    if not material:
        return ""
    status = _material_status_key(material)
    reason = escape((material.review_reason or "").strip())
    reason_html = f"<p>{reason}</p>" if reason else ""
    return f"""
    <section class="notice {escape(status.replace("_", "-"))}">
      <strong>材料已提交：{escape(_material_name(uploaded))}</strong>
      <span>{_status(status)}</span>
      {reason_html}
    </section>
    """


def _follow_up_list(items: list[RequiredMaterial]) -> str:
    if not items:
        return _empty("暂无入职后关注事项。")
    return '<div class="follow-list">' + "".join(
        f"""
        <article class="follow-item">
          <strong>{escape(item.name)}</strong>
          <p>{escape(item.description)}</p>
        </article>
        """
        for item in items
    ) + "</div>"


def _latest_materials_by_type(materials: list[MaterialSubmission]) -> dict[str, MaterialSubmission]:
    latest: dict[str, MaterialSubmission] = {}
    for material in sorted(materials, key=lambda item: item.created_at):
        latest[material.material_type] = material
    return latest


def _material_status_key(material: Optional[MaterialSubmission]) -> str:
    if not material:
        return "not_submitted"
    return _material_display_status(material.review_status, material.review_reason or "")


def _material_display_status(status: str, reason: str) -> str:
    if status in {"auto_rejected", "rejected"} and _is_supplement_reason(reason):
        return "needs_supplement"
    return status


def _is_supplement_reason(reason: str) -> bool:
    return any(keyword in reason for keyword in ("还缺", "继续上传缺少", "请继续上传"))


def _material_upload_action(material: Optional[MaterialSubmission]) -> str:
    if not material:
        return "上传并审核"
    reason = material.review_reason or ""
    if _is_supplement_reason(reason):
        return "继续上传"
    return "重新上传"


def _candidate_table(cases: list[CandidateCase]) -> str:
    if not cases:
        return _empty("暂无候选人。")
    department_options = _merged_options((case.department for case in cases), DEPARTMENT_OPTIONS)
    position_options = _merged_options((case.position for case in cases), POSITION_OPTIONS)
    rows = []
    for case in cases:
        overdue = sum(1 for node in case.nodes if node.status == "overdue")
        pending_materials = sum(1 for item in case.materials if item.review_status in {"manual_review", "auto_rejected", "rejected"})
        current_node = _find_case_node(case, case.current_stage) or _next_case_node(case)
        form_id = f"hr-case-form-{case.id}"
        current_stage = current_node.node_name if current_node else case.current_stage
        current_status = current_node.status if current_node else "pending"
        rows.append(
            f"""
            <tr>
              <td class="candidate-cell">
                <a href="/ui/candidate/{case.id}">{escape(case.candidate_name)}</a>
                <form id="{form_id}" class="hr-case-form" action="/ui/hr/cases/{case.id}/status"></form>
                <span class="form-status" data-form-status="{form_id}" aria-live="polite"></span>
              </td>
              <td>{_select("department", department_options, case.department, form_id=form_id)}</td>
              <td>{_select("position", position_options, case.position, form_id=form_id)}</td>
              <td>{escape(case.city or "-")}</td>
              <td>{_date(case.expected_onboard_date)}</td>
              <td>
                <div class="stage-controls">
                  {_current_stage_select(case, current_stage, form_id)}
                  {_select("node_status", [(status, _label(status)) for status in NODE_STATUS_OPTIONS], current_status, form_id=form_id)}
                </div>
              </td>
              <td>{_select("overall_status", [(status, _label(status)) for status in CASE_STATUS_OPTIONS], case.overall_status, form_id=form_id)}</td>
              <td>{_select("risk_level", [(level, _label(level)) for level in RISK_LEVEL_OPTIONS], case.risk_level, form_id=form_id)}</td>
              <td>{overdue}</td>
              <td>{pending_materials}</td>
            </tr>
            """
        )
    return f"""
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>候选人</th><th>部门</th><th>岗位</th><th>城市</th><th>预计入职</th>
            <th>当前节点</th><th>状态</th><th>风险</th><th>超时</th><th>材料待处理</th>
          </tr>
        </thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
    """


def _candidate_create_form() -> str:
    default_line = "产品与技术"
    default_position = BUSINESS_LINE_POSITIONS[default_line][0]
    return f"""
    <form class="candidate-create-form is-collapsed" action="/api/admin/cases">
      <div class="create-form-grid">
        <label>
          <span>候选人姓名</span>
          <input name="candidate_name" required placeholder="例如：张三">
        </label>
        <label>
          <span>预计入职</span>
          <input name="expected_onboard_date" type="date" required>
        </label>
        <label>
          <span>条线</span>
          {_form_select("department", [(line, line) for line in BUSINESS_LINE_POSITIONS], default_line, extra_attrs='data-controls-positions="true"')}
        </label>
        <label>
          <span>岗位/板块</span>
          {_form_select("position", [(position, position) for position in BUSINESS_LINE_POSITIONS[default_line]], default_position, extra_attrs='data-line-position-select="true"')}
        </label>
        <label>
          <span>城市</span>
          <input name="city" placeholder="例如：上海">
        </label>
        <label>
          <span>入职类型</span>
          {_form_select("employment_type", [(item, item) for item in EMPLOYMENT_TYPE_OPTIONS], "社招")}
        </label>
        <label>
          <span>候选人 open_id</span>
          <input name="feishu_open_id" placeholder="建群必填；ou_xxx">
        </label>
        <label>
          <span>HRBP open_id</span>
          <input name="hrbp_open_id" placeholder="建群需 HRBP 或负责人">
        </label>
        <label>
          <span>负责人 open_id</span>
          <input name="manager_open_id" placeholder="建群需 HRBP 或负责人">
        </label>
        <label>
          <span>手机号</span>
          <input name="mobile" placeholder="可选">
        </label>
        <label>
          <span>邮箱</span>
          <input name="email" type="email" placeholder="可选">
        </label>
      </div>
      <div class="create-form-footer">
        <p>建群前必须有候选人 open_id，并至少填写 HRBP/负责人 open_id。批量导入时，候选人手机号/邮箱只能匹配同组织飞书账号；个人机器人或跨组织用户通常无法匹配。</p>
        <div>
          <span class="form-status" data-create-case-status aria-live="polite"></span>
          <button type="submit">创建并同步</button>
        </div>
      </div>
    </form>
    """


def _candidate_import_form() -> str:
    return """
    <form class="candidate-import-form is-collapsed" action="/api/admin/cases/import-excel">
      <div class="import-form-main">
        <div>
          <h3>批量导入候选人</h3>
          <p>请使用固定模板；候选人 open_id 可留空，系统会尝试用候选人手机号或邮箱匹配同组织飞书账号。HRBP/负责人 open_id 仍需填写，否则不能一键建群。</p>
        </div>
        <div class="import-form-actions">
          <a class="secondary-action" href="/ui/hr/import-template">下载模板</a>
          <label class="file-control import-file-control">
            <input type="file" name="file" accept=".xlsx" required>
            <span>选择 Excel</span>
          </label>
          <button type="submit">导入并同步</button>
        </div>
      </div>
      <p class="form-status" data-import-case-status aria-live="polite"></p>
    </form>
    """


def _candidate_line_table(cases: list[CandidateCase]) -> str:
    if not cases:
        return _empty("暂无候选人。")
    rows = []
    for case in cases:
        overdue = sum(1 for node in case.nodes if node.status == "overdue")
        pending_materials = sum(1 for item in case.materials if item.review_status in {"manual_review", "auto_rejected", "rejected"})
        current_node = _find_case_node(case, case.current_stage) or _next_case_node(case)
        form_id = f"hr-workspace-case-form-{case.id}"
        current_stage = current_node.node_name if current_node else case.current_stage
        current_status = current_node.status if current_node else "pending"
        line = _case_business_line(case)
        position = _case_line_position(case, line)
        chat_action = _chat_action_cell(case)
        rows.append(
            f"""
            <tr class="hr-edit-row" data-case-row="{case.id}">
              <td class="candidate-cell">
                <a href="/ui/candidate/{case.id}/workspace">{escape(case.candidate_name)}</a>
                <button type="button" class="danger-link" data-delete-case="/api/admin/cases/{case.id}" data-candidate-name="{escape(case.candidate_name)}">删除</button>
                <form id="{form_id}" class="hr-case-form" action="/ui/hr/cases/{case.id}/status" data-manual-save="true"></form>
                <span class="form-status" data-form-status="{form_id}" aria-live="polite"></span>
              </td>
              <td class="line-cell">{_business_line_select(line, form_id)}</td>
              <td class="position-cell">{_line_position_select(line, position, form_id)}</td>
              <td>{escape(case.city or "-")}</td>
              <td>{_date(case.expected_onboard_date)}</td>
              <td>
                <div class="stage-controls">
                  {_current_stage_select(case, current_stage, form_id)}
                  {_select("node_status", [(status, _label(status)) for status in NODE_STATUS_OPTIONS], current_status, form_id=form_id)}
                </div>
              </td>
              <td>{_select("overall_status", [(status, _label(status)) for status in CASE_STATUS_OPTIONS], case.overall_status, form_id=form_id)}</td>
              <td>{_select("risk_level", [(level, _label(level)) for level in RISK_LEVEL_OPTIONS], case.risk_level, form_id=form_id)}</td>
              <td>{overdue}</td>
              <td>{pending_materials}</td>
              <td>{chat_action}</td>
              <td><button type="submit" class="hr-save-row" form="{form_id}" disabled>保存</button></td>
            </tr>
            """
        )
    return f"""
    <div class="table-wrap hr-line-table-wrap">
      <table class="hr-line-table">
        <thead>
          <tr>
            <th>候选人</th><th>条线</th><th>岗位/板块</th><th>城市</th><th>预计入职</th>
            <th>当前节点</th><th>状态</th><th>风险</th><th>超时</th><th>材料待处理</th><th>入职群</th><th>操作</th>
          </tr>
        </thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
    """


def _chat_action_cell(case: CandidateCase) -> str:
    if case.feishu_chat_id:
        return f"<span class='chat-id'>{escape(case.feishu_chat_id)}</span>"
    missing = []
    if not case.feishu_open_id:
        missing.append("候选人")
    if not (case.hrbp_open_id or case.manager_open_id):
        missing.append("HRBP/负责人")
    disabled = " disabled" if missing else ""
    title = f"缺少 {'、'.join(missing)} open_id" if missing else "创建候选人入职协同群"
    reason = f"<span class='chat-missing-reason'>缺{escape('、'.join(missing))} open_id</span>" if missing else ""
    return f"""
      <div class="chat-action-stack">
        <button type="button" class="chat-action" data-create-chat="/api/admin/cases/{case.id}/feishu-chat"
          data-candidate-name="{escape(case.candidate_name)}" title="{escape(title)}"{disabled}>创建群</button>
        {reason}
      </div>
    """


def _business_line_select(selected: str, form_id: str) -> str:
    options = [(line, line) for line in BUSINESS_LINE_POSITIONS]
    return _select("department", options, selected, form_id=form_id, extra_attrs='data-controls-positions="true"')


def _line_position_select(line: str, selected: str, form_id: str) -> str:
    options = [(position, position) for position in BUSINESS_LINE_POSITIONS.get(line, POSITION_OPTIONS)]
    return _select("position", options, selected, form_id=form_id, extra_attrs='data-line-position-select="true"')


def _form_select(name: str, options: list[tuple[str, str]], selected: str | None, *, extra_attrs: str = "") -> str:
    option_html = []
    for value, label in options:
        selected_attr = " selected" if value == selected else ""
        option_html.append(f'<option value="{escape(value)}"{selected_attr}>{escape(label)}</option>')
    attrs = f" {extra_attrs}" if extra_attrs else ""
    return f'<select class="hr-inline-select" name="{escape(name)}"{attrs}>{"".join(option_html)}</select>'


def _case_business_line(case: CandidateCase) -> str:
    text = f"{case.department or ''} {case.position or ''}"
    for line in BUSINESS_LINE_POSITIONS:
        if line in text:
            return line
    if any(keyword in text for keyword in ("财富", "投顾", "顾问")):
        return "客户成功"
    if any(keyword in text for keyword in ("市场", "零售", "机构", "养老金", "海外")):
        return "业务运营"
    if "投资" in text:
        return "产品与技术"
    if any(keyword in text for keyword in ("基础", "合规", "风控", "科技", "数据", "运营", "产品", "研究")):
        return "组织支持"
    return "产品与技术"


def _case_line_position(case: CandidateCase, line: str) -> str:
    text = case.position or ""
    for position in BUSINESS_LINE_POSITIONS[line]:
        if position == text or position.replace("板块", "") in text:
            return position
    keyword_positions = {
        "产品与技术": (
            ("固定", "后端工程"),
            ("指数", "前端工程"),
            ("多资产", "数据工程"),
            ("配置", "AI 应用"),
            ("量化", "质量保障"),
            ("国际", "开发者关系"),
            ("另类", "安全工程"),
        ),
        "业务运营": (("零售", "客户支持"), ("一般机构", "项目运营"), ("养老金", "内容运营"), ("海外", "社区运营")),
        "组织支持": (
            ("金融科技", "IT 支持"),
            ("全栈", "IT 支持"),
            ("工程", "IT 支持"),
            ("技术", "IT 支持"),
            ("合规", "People Operations"),
            ("风控", "People Operations"),
            ("数据", "数据治理"),
            ("发展研究", "战略与研究"),
            ("产品", "商务运营"),
            ("基金运营", "财务运营"),
            ("运营", "行政运营"),
        ),
        "客户成功": (("顾问", "客户成功"),),
    }
    for keyword, position in keyword_positions[line]:
        if keyword in text:
            return position
    return BUSINESS_LINE_POSITIONS[line][0]


def _current_stage_select(case: CandidateCase, selected: str | None, form_id: str) -> str:
    ordered = sorted(case.nodes, key=lambda node: (NODE_ORDER.index(node.node_name) if node.node_name in NODE_ORDER else 99, node.due_date))
    option_html = []
    for node in ordered:
        selected_attr = " selected" if node.node_name == selected else ""
        option_html.append(
            f'<option value="{escape(node.node_name)}" data-node-status="{escape(node.status)}"{selected_attr}>{_node_label(node.node_name)}</option>'
        )
    return f'<select class="hr-inline-select" name="current_stage" form="{escape(form_id)}">{"".join(option_html)}</select>'


def _select(
    name: str,
    options: list[tuple[str, str]],
    selected: str | None,
    *,
    form_id: str | None = None,
    extra_attrs: str = "",
) -> str:
    option_html = []
    for value, label in options:
        selected_attr = " selected" if value == selected else ""
        option_html.append(f'<option value="{escape(value)}"{selected_attr}>{escape(label)}</option>')
    form_attr = f' form="{escape(form_id)}"' if form_id else ""
    class_attr = ' class="hr-inline-select"' if form_id else ""
    attrs = f" {extra_attrs}" if extra_attrs else ""
    return f'<select{class_attr} name="{escape(name)}"{form_attr}{attrs}>{"".join(option_html)}</select>'


def _merged_options(values: Any, defaults: tuple[str, ...]) -> list[tuple[str, str]]:
    ordered: list[str] = []
    for value in [*defaults, *values]:
        text = str(value or "").strip()
        if text and text not in ordered:
            ordered.append(text)
    return [(value, value) for value in ordered]


def _find_case_node(case: CandidateCase, node_name: str | None) -> WorkflowNode | None:
    if not node_name:
        return None
    return next((node for node in case.nodes if node.node_name == node_name), None)


def _next_case_node(case: CandidateCase) -> WorkflowNode | None:
    ordered = sorted(case.nodes, key=lambda node: (node.due_date, node.id or 0))
    return next((node for node in ordered if node.status not in {"submitted", "approved", "skipped"}), None)


def _apply_node_status(node: WorkflowNode, status: str) -> None:
    node.status = status
    if status == "approved" and not node.completed_at:
        node.completed_at = datetime.utcnow()
    if status == "overdue":
        node.overdue_days = max(days_overdue(node.due_date), 1)
        node.escalation_level = 2 if node.overdue_days >= 3 else 1
        return
    node.overdue_days = 0
    node.escalation_level = 0
    if status != "approved":
        node.completed_at = None


def _overdue_list(nodes: list[WorkflowNode]) -> str:
    if not nodes:
        return _empty("当前没有超时节点。")
    return '<div class="stack">' + "".join(
        f"""
        <article class="item">
          <div>
            <strong>{escape(node.case.candidate_name)}</strong>
            <p>{_node_label(node.node_name)} · 截止 {_date(node.due_date)}</p>
          </div>
          <div class="right">
            {_status("overdue")}
            <span class="muted">{node.overdue_days} 天</span>
          </div>
        </article>
        """
        for node in nodes
    ) + "</div>"


def _material_list(materials: list[MaterialSubmission], *, compact: bool = True) -> str:
    if not materials:
        return _empty("暂无材料审核记录。")
    ordered = sorted(materials, key=lambda material: material.created_at or datetime.min, reverse=True)
    return '<div class="stack">' + "".join(_material_item(material, compact=compact) for material in ordered) + "</div>"


def _material_item(material: MaterialSubmission, *, compact: bool) -> str:
    standard = get_material_standard(material.material_type)
    name = standard.display_name if standard else _material_name(material.material_type)
    reason = escape((material.review_reason or "").strip())
    reason_html = f"<p>{reason}</p>" if reason and not compact else ""
    owner = f"<span class='muted'>{escape(material.case.candidate_name)}</span>" if compact else ""
    return f"""
    <article class="item">
      <div>
        <strong>{escape(name)}</strong>
        <p>{escape(material.file_name or "未记录文件名")} {owner}</p>
        {reason_html}
      </div>
      <div class="right">{_status(_material_status_key(material))}</div>
    </article>
    """


def _timeline(nodes: list[WorkflowNode]) -> str:
    ordered = sorted(nodes, key=lambda node: (NODE_ORDER.index(node.node_name) if node.node_name in NODE_ORDER else 99, node.due_date))
    groups: dict[str, list[WorkflowNode]] = {}
    for node in ordered:
        group = NODE_GROUP_NAMES.get(node.node_name) or _node_label(node.node_name)
        groups.setdefault(group, []).append(node)

    parts = []
    for group, group_nodes in groups.items():
        if len(group_nodes) == 1 and group == NODE_DISPLAY_NAMES.get(group_nodes[0].node_name, group_nodes[0].node_name):
            node = group_nodes[0]
            parts.append(
                f"""
                <section class='timeline-group single'>
                  <div class="timeline-row" id="node-{escape(node.node_name)}" data-node-name="{escape(node.node_name)}">
                    <span class="dot {escape(node.status)}"></span>
                    <div>
                      <strong>{_node_label(node.node_name)}</strong>
                      <p><span class="node-status-line">{_date(node.due_date)} · {_label(node.status)}</span></p>
                      {_node_confirm_form(node)}
                    </div>
                  </div>
                </section>
                """
            )
            continue
        children = "".join(
            f"""
            <div class="timeline-row" id="node-{escape(node.node_name)}" data-node-name="{escape(node.node_name)}">
              <span class="dot {escape(node.status)}"></span>
              <div>
                <strong>{_node_label(node.node_name)}</strong>
                <p><span class="node-status-line">{_date(node.due_date)} · {_label(node.status)}</span></p>
                {_node_confirm_form(node)}
              </div>
            </div>
            """
            for node in group_nodes
        )
        parts.append(f"<section class='timeline-group'><h3>{escape(group)}</h3>{children}</section>")
    return "<div class='timeline'>" + "".join(parts) + "</div>"


def _node_confirm_form(node: WorkflowNode) -> str:
    if node.status in {"submitted", "approved", "skipped"}:
        return ""
    action = "确认已提交" if NODE_GROUP_NAMES.get(node.node_name) == "材料递交" else "确认已完成"
    message = f"确认{_node_label(node.node_name)}已经完成或提交？系统会记录为已确认，并通知复核。"
    return f"""
    <form class="node-confirm-form" action="/ui/candidate/{node.case_id}/nodes/{escape(node.node_name)}/confirm" data-node-name="{escape(node.node_name)}" data-confirm-message="{escape(message)}">
      <button type="submit">{action}</button>
      <span class="confirm-status" aria-live="polite"></span>
    </form>
    """


def _page(*, title: str, body: str) -> str:
    return f"""
    <!doctype html>
    <html lang="zh-CN">
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>{escape(title)}</title>
        <style>{_css()}</style>
      </head>
      <body>{body}<script>{_js()}</script></body>
    </html>
    """


def _metric(label: str, value: object, *, key: str | None = None) -> str:
    key_attr = f" data-metric-key='{escape(key)}'" if key else ""
    return f"<div class='metric'{key_attr}><span>{escape(label)}</span><strong>{escape(str(value))}</strong></div>"


def _status(value: str) -> str:
    value_class = value.replace("_", "-")
    return f"<span class='pill {escape(value_class)}'>{_label(value)}</span>"


def _label(value: str) -> str:
    return escape(STATUS_LABELS.get(value, value))


def _node_label(node_name: str) -> str:
    return escape(NODE_DISPLAY_NAMES.get(node_name, node_name))


def _date(value: Optional[date]) -> str:
    return escape(value.isoformat() if value else "-")


def _empty(text: str) -> str:
    return f"<div class='empty'>{escape(text)}</div>"


def _decode_upload_data(data_base64: str) -> bytes:
    try:
        file_bytes = base64.b64decode(data_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="invalid file data") from exc
    if not file_bytes:
        raise HTTPException(status_code=400, detail="empty file")
    max_upload_bytes = get_settings().max_upload_bytes
    if len(file_bytes) > max_upload_bytes:
        raise HTTPException(status_code=400, detail=f"file exceeds {max_upload_bytes} byte limit")
    return file_bytes


def _safe_file_name(file_name: str) -> str:
    name = Path(file_name or "upload.bin").name.strip() or "upload.bin"
    name = re.sub(r"[^\w.\-\u4e00-\u9fff]+", "_", name, flags=re.UNICODE)
    return name[:120] or "upload.bin"


def _validate_upload_content(
    material_type: str,
    file_name: str,
    content_type: str | None,
    content: bytes,
) -> None:
    standard = get_material_standard(material_type)
    allowed = {item.lower().lstrip(".") for item in (standard.accepted_formats if standard else ())}
    extension = Path(file_name).suffix.lower().lstrip(".")
    if not extension or extension not in allowed:
        raise HTTPException(status_code=400, detail="file extension is not allowed for this material")

    normalized_type = (content_type or "").split(";", 1)[0].strip().lower()
    expected_types = MIME_BY_EXTENSION.get(extension, set())
    if normalized_type and normalized_type not in expected_types:
        raise HTTPException(status_code=400, detail="file MIME type does not match its extension")

    signatures = {
        "pdf": (b"%PDF-",),
        "png": (b"\x89PNG\r\n\x1a\n",),
        "jpg": (b"\xff\xd8\xff",),
        "jpeg": (b"\xff\xd8\xff",),
        "doc": (b"\xd0\xcf\x11\xe0",),
        "docx": (b"PK\x03\x04",),
    }
    if not any(content.startswith(signature) for signature in signatures.get(extension, ())):
        raise HTTPException(status_code=400, detail="file content does not match its extension")


def _upload_path(case_id: int, material_type: str, file_name: str) -> Path:
    configured_root = UPLOAD_ROOT if UPLOAD_ROOT != DEFAULT_UPLOAD_ROOT else Path(get_settings().upload_root)
    root = configured_root.resolve()
    safe_material_type = re.sub(r"[^a-zA-Z0-9_-]", "_", material_type)[:64]
    destination = root / f"case_{case_id}" / safe_material_type / f"{time.time_ns()}_{secrets.token_hex(4)}_{file_name}"
    resolved = destination.resolve()
    if not resolved.is_relative_to(root):
        raise HTTPException(status_code=400, detail="invalid upload path")
    return resolved


def _accept_attr(formats: tuple[str, ...]) -> str:
    if not formats:
        return ""
    accepted = ",".join(f".{item.lower().lstrip('.')}" for item in formats)
    return f'accept="{escape(accepted)}"'


def _material_name(material_type: str) -> str:
    standard = get_material_standard(material_type)
    if standard:
        return standard.display_name
    for employment_type in ("社招", "校招-境内", "校招-境外"):
        for item in required_materials_for_employment_type(employment_type):
            if item.code == material_type:
                return item.name
    return material_type


def _js() -> str:
    line_position_options = json.dumps(BUSINESS_LINE_POSITIONS, ensure_ascii=False)
    return (
        f"const LINE_POSITION_OPTIONS = {line_position_options};\n"
        + r"""
    const noticeList = document.querySelector(".notice-list");

    const statusClass = (status) => String(status || "pending").replaceAll("_", "-");
    const IMAGE_UPLOAD_TARGET_BYTES = 900 * 1024;
    const IMAGE_UPLOAD_MIN_BYTES = 205 * 1024;
    let uploadQueue = Promise.resolve();
    let queuedUploadCount = 0;
    const bulkSubmitPanel = document.querySelector("[data-bulk-submit-panel]");
    const bulkSubmitCount = document.querySelector("[data-bulk-submit-count]");
    const bulkSubmitList = document.querySelector(".bulk-submit-list");
    const bulkSubmitButton = document.querySelector("[data-submit-all]");
    const bulkClearButton = document.querySelector("[data-clear-bulk-submit]");
    const bulkSubmitQueue = new Map();

    function buildMaterialResult(status, label, message) {
      const result = document.createElement("div");
      result.className = "material-result " + statusClass(status);
      const title = document.createElement("strong");
      title.textContent = label || "材料已提交";
      const text = document.createElement("p");
      text.textContent = message || "材料已提交，请等待审核。";
      result.append(title, text);
      return result;
    }

    function buildNotice(payload) {
      const notice = document.createElement("section");
      notice.className = "notice " + statusClass(payload.review_status);
      notice.dataset.materialType = payload.material_type || "";
      const title = document.createElement("strong");
      const titlePrefix = payload.summary_title || "材料已提交";
      const titleTarget = payload.material_name || payload.file_name || (payload.material_count ? payload.material_count + " 个材料项" : "材料");
      title.textContent = titlePrefix + "：" + titleTarget;
      const pill = document.createElement("span");
      pill.className = "pill " + statusClass(payload.review_status);
      pill.textContent = payload.review_label || "已提交";
      notice.append(title, pill);
      if (payload.review_reason || payload.message) {
        const reason = document.createElement("p");
        reason.textContent = payload.review_reason || payload.message;
        notice.append(reason);
      }
      return notice;
    }

    function metricValue(key) {
      const metric = document.querySelector('[data-metric-key="' + key + '"] strong');
      return metric || null;
    }

    function setMetricValue(key, value) {
      const node = metricValue(key);
      if (node) node.textContent = String(value);
    }

    function cardStatus(card) {
      const pill = card.querySelector(".material-top .pill");
      if (!pill) return "not-submitted";
      const classes = Array.from(pill.classList);
      if (classes.includes("auto-approved")) return "auto-approved";
      if (classes.includes("approved")) return "approved";
      if (classes.includes("needs-supplement")) return "needs-supplement";
      if (classes.includes("auto-rejected")) return "auto-rejected";
      if (classes.includes("rejected")) return "rejected";
      if (classes.includes("manual-review")) return "manual-review";
      if (classes.includes("not-submitted")) return "not-submitted";
      return "submitted";
    }

    function refreshMaterialMetrics() {
      const cards = Array.from(document.querySelectorAll(".material-card"));
      const statuses = cards.map(cardStatus);
      setMetricValue("required", cards.length);
      setMetricValue("not_submitted", statuses.filter((status) => status === "not-submitted").length);
      setMetricValue("approved", statuses.filter((status) => status === "approved" || status === "auto-approved").length);
      setMetricValue("needs_fix", statuses.filter((status) => ["rejected", "auto-rejected", "manual-review", "needs-supplement"].includes(status)).length);
    }

    function formatBytes(bytes) {
      if (bytes >= 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + "MB";
      return Math.max(1, Math.round(bytes / 1024)) + "KB";
    }

    function enqueueUpload(work, status) {
      const position = queuedUploadCount;
      queuedUploadCount += 1;
      if (position > 0 && status) {
        status.textContent = "已加入审核队列，前面还有 " + position + " 个文件。";
      }
      const run = uploadQueue.catch(() => {}).then(async () => {
        queuedUploadCount = Math.max(queuedUploadCount - 1, 0);
        return work();
      });
      uploadQueue = run.catch(() => {});
      return run;
    }

    function materialNameForForm(form) {
      const title = form.closest(".material-card")?.querySelector(".material-title h3");
      return title ? title.textContent.trim() : form.dataset.materialType || "材料";
    }

    function resetFileInput(input, label, labelText) {
      if (input) input.value = "";
      if (labelText) labelText.textContent = "选择文件";
      if (label) {
        label.title = "";
        label.classList.remove("selected");
      }
    }

    function validateUploadFiles(files, status) {
      if (!files.length) {
        if (status) status.textContent = "请先选择文件。";
        return false;
      }
      const oversizeFile = files.find((file) => file.size > 12 * 1024 * 1024 && !(file.type || "").startsWith("image/"));
      if (oversizeFile) {
        if (status) status.textContent = oversizeFile.name + " 超过 12MB，请压缩后重新上传。";
        return false;
      }
      return true;
    }

    function renderBulkSubmitQueue() {
      if (!bulkSubmitPanel || !bulkSubmitCount || !bulkSubmitList || !bulkSubmitButton || !bulkClearButton) return;
      const entries = Array.from(bulkSubmitQueue.values());
      const fileCount = entries.reduce((total, entry) => total + entry.files.length, 0);
      bulkSubmitCount.textContent = "待提交 " + entries.length + " 个材料项、" + fileCount + " 个文件";
      if (!entries.length) {
        bulkSubmitList.textContent = "可先在多个材料项中选择文件并加入待提交。";
      } else {
        bulkSubmitList.replaceChildren(...entries.map((entry) => {
          const item = document.createElement("span");
          item.textContent = entry.materialName + " " + entry.files.length + " 个文件";
          return item;
        }));
      }
      bulkSubmitButton.disabled = entries.length === 0;
      bulkClearButton.disabled = entries.length === 0;
    }

    function queueFormFiles(form, input, label, labelText) {
      const status = form.querySelector(".upload-status");
      const files = input && input.files ? Array.from(input.files) : [];
      if (!validateUploadFiles(files, status)) return;
      const materialType = form.dataset.materialType;
      bulkSubmitQueue.set(materialType, {
        form,
        materialType,
        materialName: materialNameForForm(form),
        files,
      });
      if (status) status.textContent = "已加入待提交，可继续选择其他材料。";
      resetFileInput(input, label, labelText);
      renderBulkSubmitQueue();
    }

    async function submitBulkQueue() {
      if (!bulkSubmitQueue.size || !bulkSubmitButton) return;
      const entries = Array.from(bulkSubmitQueue.values());
      const defaultText = bulkSubmitButton.textContent;
      bulkSubmitButton.disabled = true;
      if (bulkClearButton) bulkClearButton.disabled = true;
      bulkSubmitButton.textContent = "提交中";
      if (bulkSubmitList) bulkSubmitList.textContent = "正在准备统一提交，请保持页面打开。";
      try {
        await enqueueUpload(async () => {
          const items = [];
          for (const entry of entries) {
            const status = entry.form.querySelector(".upload-status");
            const preparedFiles = [];
            for (let index = 0; index < entry.files.length; index += 1) {
              const file = entry.files[index];
              if (status) status.textContent = "统一提交准备中：" + (index + 1) + "/" + entry.files.length;
              const prepared = await prepareUploadFile(file, status);
              if (prepared.size > 12 * 1024 * 1024) {
                throw new Error(prepared.fileName + " 超过 12MB，请压缩后重新上传。");
              }
              preparedFiles.push({
                file_name: prepared.fileName,
                content_type: prepared.contentType,
                data_base64: prepared.base64,
              });
            }
            items.push({material_type: entry.materialType, files: preparedFiles});
          }
          if (bulkSubmitList) bulkSubmitList.textContent = "正在上传并审核 " + items.length + " 个材料项。";
          const response = await fetch(window.location.pathname + "/batch-submit", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({items}),
          });
          const payload = await response.json().catch(() => ({}));
          if (!response.ok) {
            throw new Error(payload.detail || "统一提交失败，请稍后重试。");
          }
          const formsByType = new Map(entries.map((entry) => [entry.materialType, entry.form]));
          if (Array.isArray(payload.groups)) {
            payload.groups.forEach((group) => {
              const form = formsByType.get(group.material_type);
              if (form) {
                updateCard(form, group);
                const status = form.querySelector(".upload-status");
                if (status) status.textContent = group.message || "审核完成。";
              }
              if (Array.isArray(group.results)) {
                group.results.forEach(updateSubmittedRecords);
              }
            });
          }
          updateNotice(payload);
          bulkSubmitQueue.clear();
          renderBulkSubmitQueue();
        }, null);
      } catch (error) {
        const message = error.message === "Failed to fetch" ? "统一提交请求未完成，可能是网络中断或服务超时，请稍后重试。" : error.message;
        if (bulkSubmitList) bulkSubmitList.textContent = message || "统一提交失败，请稍后重试。";
      } finally {
        bulkSubmitButton.textContent = defaultText || "提交全部";
        renderBulkSubmitQueue();
      }
    }

    function updateCard(form, payload) {
      const card = form.closest(".material-card");
      if (!card) return;
      const pill = card.querySelector(".material-top .pill");
      if (pill) {
        pill.className = "pill " + statusClass(payload.review_status);
        pill.textContent = payload.review_label || "已提交";
      }
      const metaRows = card.querySelectorAll(".material-meta span");
      if (metaRows.length) {
        metaRows[metaRows.length - 1].textContent = "文件状态：" + (payload.file_name || "已提交");
      }
      const oldGuidance = card.querySelector(".material-result, .rule-list");
      const nextGuidance = buildMaterialResult(payload.review_status, payload.review_label, payload.review_reason || payload.message);
      if (oldGuidance) {
        oldGuidance.replaceWith(nextGuidance);
      } else {
        form.before(nextGuidance);
      }
      const button = form.querySelector('button[type="submit"]');
      if (button) {
        button.dataset.defaultText = statusClass(payload.review_status) === "needs-supplement" ? "继续上传" : "重新上传";
      }
      refreshMaterialMetrics();
    }

    function refreshCollapsiblePanel(panel) {
      const list = panel.querySelector(".collapsible-list, .submitted-records-list");
      const button = panel.querySelector(".panel-toggle, .history-toggle");
      if (!list || !button) return;
      const expanded = list.classList.contains("is-expanded");
      button.textContent = expanded ? "收起" : "展开";
      button.setAttribute("aria-expanded", String(expanded));
      button.hidden = !expanded && list.scrollHeight <= list.clientHeight + 4;
    }

    function syncCandidateMaterialPanel() {
      const flowPanel = document.querySelector(".candidate-layout .flow-panel");
      const materialPanel = document.querySelector(".candidate-layout .material-status-panel");
      if (!flowPanel || !materialPanel) return;
      const list = materialPanel.querySelector(".candidate-material-list");
      const head = materialPanel.querySelector(".panel-head");
      if (!list || !head) return;
      if (window.matchMedia("(max-width: 860px)").matches) {
        materialPanel.style.removeProperty("max-height");
        list.style.removeProperty("--collapsible-max-height");
        refreshCollapsiblePanel(materialPanel);
        return;
      }
      const panelHeight = Math.max(260, flowPanel.offsetHeight);
      const listHeight = Math.max(180, panelHeight - head.offsetHeight);
      materialPanel.style.maxHeight = panelHeight + "px";
      list.style.setProperty("--collapsible-max-height", listHeight + "px");
      refreshCollapsiblePanel(materialPanel);
    }

    function syncWorkspaceSubmittedRecords() {
      const submitted = document.querySelector(".workspace-submitted-records");
      const materials = document.querySelector(".workspace-materials");
      if (!submitted || !materials) return;
      const list = submitted.querySelector(".submitted-records-list");
      const head = submitted.querySelector(".panel-head");
      if (!list || !head) return;
      if (window.matchMedia("(max-width: 860px)").matches) {
        submitted.style.removeProperty("height");
        list.style.setProperty("--workspace-submitted-list-height", "min(360px, 52vh)");
        return;
      }
      const materialsBottom = materials.getBoundingClientRect().bottom;
      const submittedTop = submitted.getBoundingClientRect().top;
      const panelHeight = Math.max(320, Math.floor(materialsBottom - submittedTop));
      const listHeight = Math.max(180, panelHeight - head.offsetHeight - 2);
      submitted.style.height = panelHeight + "px";
      list.style.setProperty("--workspace-submitted-list-height", listHeight + "px");
    }

    function updateSubmittedRecords(payload) {
      if (!payload.record_html) return;
      const panel = document.querySelector(".submitted-records");
      if (!panel) return;
      const list = panel.querySelector(".submitted-records-list");
      if (!list) return;
      let stack = list.querySelector(".stack");
      if (!stack) {
        list.innerHTML = '<div class="stack"></div>';
        stack = list.querySelector(".stack");
      }
      const holder = document.createElement("div");
      holder.innerHTML = payload.record_html.trim();
      const item = holder.firstElementChild;
      if (item) {
        stack.prepend(item);
      }
      const count = stack.querySelectorAll(".item").length;
      const countLabel = panel.querySelector(".submitted-count");
      if (countLabel) {
        countLabel.textContent = count + " 条";
      }
      refreshCollapsiblePanel(panel);
      syncWorkspaceSubmittedRecords();
    }

    function updateNotice(payload) {
      if (!noticeList) return;
      const notice = buildNotice(payload);
      noticeList.replaceChildren(notice);
    }

    function showPageNotice(message, status) {
      if (!noticeList) return;
      const notice = document.createElement("section");
      notice.className = "notice " + statusClass(status || "submitted");
      const title = document.createElement("strong");
      title.textContent = message || "已确认。";
      notice.append(title);
      noticeList.replaceChildren(notice);
    }

    async function submitNodeConfirmation(form, promptText) {
      const message = promptText || form.dataset.confirmMessage || "确认该节点已经完成或提交？";
      if (!window.confirm(message)) return;
      const button = form.querySelector('button[type="submit"]');
      const status = form.querySelector(".confirm-status");
      if (button) button.disabled = true;
      if (status) status.textContent = "正在确认...";
      try {
        const response = await fetch(form.action, {method: "POST"});
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.detail || "确认失败，请稍后重试。");
        const row = form.closest(".timeline-row");
        if (row) {
          const dot = row.querySelector(".dot");
          if (dot) dot.className = "dot " + statusClass(payload.node_status || "submitted");
          const line = row.querySelector(".node-status-line");
          if (line) {
            const dueText = line.textContent.split("·")[0].trim();
            line.textContent = dueText + " · " + (payload.node_status_label || "已确认");
          }
        }
        form.remove();
        showPageNotice(payload.message || "已确认，系统会通知复核。", payload.node_status || "submitted");
      } catch (error) {
        if (status) status.textContent = error.message || "确认失败，请稍后重试。";
        if (button) button.disabled = false;
      }
    }

    function hrControls(form) {
      return Array.from(document.querySelectorAll('[form="' + form.id + '"]'));
    }

    function setHrFormDirty(form, dirty) {
      const row = form.closest("tr");
      const status = document.querySelector('[data-form-status="' + form.id + '"]');
      const saveButton = document.querySelector('.hr-save-row[form="' + form.id + '"]');
      if (row) row.classList.toggle("is-dirty", dirty);
      if (saveButton) saveButton.disabled = !dirty;
      if (dirty && status) status.textContent = "有未保存修改";
      if (!dirty && status && status.textContent === "有未保存修改") status.textContent = "";
    }

    async function submitHrCaseForm(form) {
      const status = document.querySelector('[data-form-status="' + form.id + '"]');
      const controls = hrControls(form);
      const payload = {};
      controls.forEach((control) => {
        if (control.name) payload[control.name] = control.value;
      });
      controls.forEach((control) => { control.disabled = true; });
      if (status) status.textContent = "保存中...";
      try {
        const response = await fetch(form.action, {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(payload),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || "保存失败，请稍后重试。");
        setHrFormDirty(form, false);
        if (status) status.textContent = data.message || "已保存。";
      } catch (error) {
        if (status) status.textContent = error.message || "保存失败，请稍后重试。";
      } finally {
        controls.forEach((control) => { control.disabled = false; });
      }
    }

    function syncNodeStatusSelect(stageSelect) {
      if (stageSelect.name !== "current_stage") return;
      const selected = stageSelect.options[stageSelect.selectedIndex];
      const status = selected ? selected.dataset.nodeStatus : "";
      const nodeStatusSelect = document.querySelector('[form="' + stageSelect.getAttribute("form") + '"][name="node_status"]');
      if (status && nodeStatusSelect) {
        nodeStatusSelect.value = status;
      }
    }

    function syncLinePositionSelect(lineSelect) {
      if (lineSelect.name !== "department" || lineSelect.dataset.controlsPositions !== "true") return;
      const formId = lineSelect.getAttribute("form");
      const positionSelect = formId
        ? document.querySelector('[form="' + formId + '"][name="position"]')
        : lineSelect.closest("form")?.querySelector('[name="position"]');
      if (!positionSelect) return;
      const options = LINE_POSITION_OPTIONS[lineSelect.value] || [];
      const previous = positionSelect.value;
      positionSelect.innerHTML = "";
      options.forEach((label) => {
        const option = document.createElement("option");
        option.value = label;
        option.textContent = label;
        positionSelect.append(option);
      });
      if (options.includes(previous)) {
        positionSelect.value = previous;
      } else if (options.length) {
        positionSelect.value = options[0];
      }
    }

    function welcomeStatusText(welcome) {
      if (!welcome || !welcome.status) return "";
      if (welcome.status === "success") return " 欢迎消息已发送。";
      if (welcome.status === "skipped") return " 未发送欢迎消息：" + (welcome.reason || "缺少候选人 open_id") + "。";
      if (welcome.status === "failed") return " 欢迎消息发送失败：" + (welcome.reason || "请检查机器人权限") + "。";
      return "";
    }

    function importWelcomeSummary(created) {
      const list = Array.isArray(created) ? created : [];
      if (!list.length) return "";
      const counts = {success: 0, failed: 0, skipped: 0};
      list.forEach((item) => {
        const status = item && item.welcome_message ? item.welcome_message.status : "";
        if (status && Object.prototype.hasOwnProperty.call(counts, status)) counts[status] += 1;
      });
      const parts = [];
      if (counts.success) parts.push("欢迎消息成功 " + counts.success + " 条");
      if (counts.failed) parts.push("失败 " + counts.failed + " 条");
      if (counts.skipped) parts.push("跳过 " + counts.skipped + " 条");
      return parts.length ? " " + parts.join("，") + "。" : "";
    }

    document.querySelectorAll(".hr-case-form").forEach((form) => {
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        submitHrCaseForm(form);
      });
      if (form.dataset.manualSave === "true") {
        setHrFormDirty(form, false);
      }
      hrControls(form).forEach((control) => {
        control.addEventListener("change", () => {
          syncLinePositionSelect(control);
          syncNodeStatusSelect(control);
          if (form.dataset.manualSave === "true") {
            setHrFormDirty(form, true);
          } else {
            submitHrCaseForm(form);
          }
        });
      });
    });

    document.querySelectorAll("[data-toggle-create-case]").forEach((button) => {
      button.addEventListener("click", () => {
        const form = document.querySelector(".candidate-create-form");
        if (!form) return;
        const collapsed = form.classList.toggle("is-collapsed");
        button.textContent = collapsed ? "新增候选人" : "收起新增";
      });
    });

    document.querySelectorAll("[data-toggle-import-cases]").forEach((button) => {
      button.addEventListener("click", () => {
        const form = document.querySelector(".candidate-import-form");
        if (!form) return;
        const collapsed = form.classList.toggle("is-collapsed");
        button.textContent = collapsed ? "批量导入" : "收起导入";
      });
    });

    document.querySelectorAll(".candidate-create-form").forEach((form) => {
      const lineSelect = form.querySelector('[name="department"]');
      if (lineSelect) {
        lineSelect.addEventListener("change", () => syncLinePositionSelect(lineSelect));
      }
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const status = form.querySelector("[data-create-case-status]");
        const button = form.querySelector('button[type="submit"]');
        const payload = {};
        new FormData(form).forEach((value, key) => {
          const text = String(value || "").trim();
          if (text) payload[key] = text;
        });
        if (status) status.textContent = "创建中...";
        if (button) button.disabled = true;
        try {
          const response = await fetch(form.action, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(payload),
          });
          const data = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(data.detail || "创建失败，请检查字段。");
          if (status) status.textContent = "已创建并尝试同步飞书多维表。" + welcomeStatusText(data.welcome_message) + " 正在刷新...";
          window.setTimeout(() => window.location.reload(), 1100);
        } catch (error) {
          if (status) status.textContent = error.message || "创建失败，请稍后重试。";
          if (button) button.disabled = false;
        }
      });
    });

    document.querySelectorAll(".candidate-import-form").forEach((form) => {
      const input = form.querySelector('input[type="file"]');
      const labelText = form.querySelector(".import-file-control span");
      const status = form.querySelector("[data-import-case-status]");
      const button = form.querySelector('button[type="submit"]');
      if (input && labelText) {
        input.addEventListener("change", () => {
          const file = input.files && input.files[0] ? input.files[0] : null;
          labelText.textContent = file ? file.name : "选择 Excel";
        });
      }
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const file = input && input.files ? input.files[0] : null;
        if (!file) {
          if (status) status.textContent = "请先选择 Excel 模板文件。";
          return;
        }
        if (status) status.textContent = "正在读取并导入...";
        if (button) button.disabled = true;
        try {
          const dataUrl = await readFileAsDataUrl(file);
          const base64 = dataUrl.includes(",") ? dataUrl.split(",").pop() : dataUrl;
          const response = await fetch(form.action, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({file_name: file.name, data_base64: base64}),
          });
          const data = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(data.detail || "导入失败，请检查模板。");
          const message = "新增 " + data.created_count + " 人，重复跳过 " + data.skipped_count + " 人，错误 " + data.error_count + " 行。" + importWelcomeSummary(data.created);
          if (status) status.textContent = message + (data.error_count ? " 请检查返回错误后重新导入。" : " 正在刷新...");
          if (data.created_count > 0) window.setTimeout(() => window.location.reload(), 1300);
        } catch (error) {
          if (status) status.textContent = error.message || "导入失败，请稍后重试。";
        } finally {
          if (button) button.disabled = false;
        }
      });
    });

    document.querySelectorAll("[data-delete-case]").forEach((button) => {
      button.addEventListener("click", async () => {
        const name = button.dataset.candidateName || "该候选人";
        if (!window.confirm("确认删除 " + name + "？本地流程、材料记录会一并删除，并尝试同步删除飞书多维表记录。")) return;
        const row = button.closest("tr");
        const status = row ? row.querySelector(".form-status") : null;
        button.disabled = true;
        if (status) status.textContent = "删除中...";
        try {
          const response = await fetch(button.dataset.deleteCase, {method: "DELETE"});
          const data = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(data.detail || "删除失败，请稍后重试。");
          if (status) status.textContent = data.message || "已删除。";
          window.setTimeout(() => window.location.reload(), 650);
        } catch (error) {
          if (status) status.textContent = error.message || "删除失败，请稍后重试。";
          button.disabled = false;
        }
      });
    });

    document.querySelectorAll("[data-create-chat]").forEach((button) => {
      button.addEventListener("click", async () => {
        const name = button.dataset.candidateName || "该候选人";
        if (!window.confirm("确认为 " + name + " 创建入职协同群？系统会邀请候选人、HRBP/负责人和机器人。")) return;
        const row = button.closest("tr");
        const status = row ? row.querySelector(".form-status") : null;
        button.disabled = true;
        button.textContent = "创建中";
        if (status) status.textContent = "正在创建飞书群...";
        try {
          const response = await fetch(button.dataset.createChat, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({force_add_members: true}),
          });
          const data = await response.json().catch(() => ({}));
          if (!response.ok || data.status === "failed") throw new Error(data.detail || data.reason || "创建群失败。");
          if (data.status === "skipped" && !data.chat_id) throw new Error(data.reason || "未创建群。");
          const chatId = data.chat_id || "";
          const label = document.createElement("span");
          label.className = "chat-id";
          label.textContent = chatId || "已处理";
          label.title = chatId;
          button.replaceWith(label);
          if (status) status.textContent = data.action === "members_added" ? "已补充群成员。" : "入职协同群已创建。";
        } catch (error) {
          button.disabled = false;
          button.textContent = "创建群";
          if (status) status.textContent = error.message || "创建群失败，请稍后重试。";
        }
      });
    });

    document.querySelectorAll(".node-confirm-form").forEach((form) => {
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        submitNodeConfirmation(form);
      });
    });

    const requestedConfirmNode = new URLSearchParams(window.location.search).get("confirm_node");
    if (requestedConfirmNode) {
      const form = Array.from(document.querySelectorAll(".node-confirm-form")).find((item) => item.dataset.nodeName === requestedConfirmNode);
      if (form) {
        form.scrollIntoView({block: "center"});
        setTimeout(() => submitNodeConfirmation(form, form.dataset.confirmMessage), 240);
      }
    }

    function readFileAsDataUrl(file) {
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || ""));
        reader.onerror = () => reject(new Error("文件读取失败"));
        reader.readAsDataURL(file);
      });
    }

    function imageBlobFromCanvas(canvas, type, quality) {
      return new Promise((resolve) => {
        canvas.toBlob((blob) => resolve(blob), type, quality);
      });
    }

    async function compressImageForUpload(file) {
      if (!/^image\/(jpeg|png)$/.test(file.type)) return null;
      if (file.size <= IMAGE_UPLOAD_TARGET_BYTES) return null;
      const dataUrl = await readFileAsDataUrl(file);
      const image = await new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = () => reject(new Error("图片读取失败"));
        img.src = dataUrl;
      });
      const sourceWidth = image.naturalWidth || image.width;
      const sourceHeight = image.naturalHeight || image.height;
      const canvas = document.createElement("canvas");
      const context = canvas.getContext("2d");
      if (!context) return null;
      let bestBlob = null;
      let bestUnderTargetBlob = null;
      for (const maxSide of [2200, 1800, 1400, 1100]) {
        const ratio = Math.min(1, maxSide / Math.max(sourceWidth, sourceHeight));
        canvas.width = Math.max(1, Math.round(sourceWidth * ratio));
        canvas.height = Math.max(1, Math.round(sourceHeight * ratio));
        context.clearRect(0, 0, canvas.width, canvas.height);
        context.drawImage(image, 0, 0, canvas.width, canvas.height);
        for (const quality of [0.86, 0.76, 0.66, 0.56]) {
          const blob = await imageBlobFromCanvas(canvas, "image/jpeg", quality);
          if (!blob) continue;
          if (blob.size < IMAGE_UPLOAD_MIN_BYTES || blob.size >= file.size) continue;
          if (!bestBlob || blob.size < bestBlob.size) bestBlob = blob;
          if (blob.size <= IMAGE_UPLOAD_TARGET_BYTES && (!bestUnderTargetBlob || blob.size < bestUnderTargetBlob.size)) {
            bestUnderTargetBlob = blob;
          }
        }
      }
      const selectedBlob = bestUnderTargetBlob || bestBlob;
      if (!selectedBlob) return null;
      const nextName = file.name.replace(/\.(png|jpe?g)$/i, ".jpg") || "upload.jpg";
      return new File([selectedBlob], nextName, {type: "image/jpeg"});
    }

    async function prepareUploadFile(file, status) {
      const compressed = await compressImageForUpload(file);
      const uploadFile = compressed || file;
      if (compressed && status) {
        status.textContent = "图片已压缩到 " + formatBytes(compressed.size) + "，正在上传并审核...";
      }
      const dataUrl = await readFileAsDataUrl(uploadFile);
      const base64 = dataUrl.includes(",") ? dataUrl.split(",").pop() : dataUrl;
      return {
        base64,
        fileName: uploadFile.name,
        contentType: uploadFile.type || file.type,
        size: uploadFile.size,
      };
    }

    document.querySelectorAll(".collapsible-panel, .submitted-records").forEach((panel) => {
      const list = panel.querySelector(".collapsible-list, .submitted-records-list");
      const button = panel.querySelector(".panel-toggle, .history-toggle");
      if (!list || !button) return;
      button.addEventListener("click", () => {
        list.classList.toggle("is-expanded");
        list.classList.toggle("is-collapsed", !list.classList.contains("is-expanded"));
        syncCandidateMaterialPanel();
        refreshCollapsiblePanel(panel);
      });
      requestAnimationFrame(() => {
        syncCandidateMaterialPanel();
        syncWorkspaceSubmittedRecords();
        refreshCollapsiblePanel(panel);
      });
    });
    requestAnimationFrame(syncWorkspaceSubmittedRecords);
    window.addEventListener("resize", () => {
      syncCandidateMaterialPanel();
      syncWorkspaceSubmittedRecords();
      document.querySelectorAll(".collapsible-panel, .submitted-records").forEach(refreshCollapsiblePanel);
    });

    if (bulkSubmitButton) {
      bulkSubmitButton.addEventListener("click", submitBulkQueue);
    }
    if (bulkClearButton) {
      bulkClearButton.addEventListener("click", () => {
        bulkSubmitQueue.clear();
        renderBulkSubmitQueue();
      });
    }
    renderBulkSubmitQueue();

    document.querySelectorAll(".upload-form").forEach((form) => {
      const input = form.querySelector('input[type="file"]');
      const label = form.querySelector(".file-control");
      const labelText = label ? label.querySelector("span") : null;
      const queueButton = form.querySelector(".queue-upload-button");
      if (input && labelText) {
        input.addEventListener("change", () => {
          const files = input.files ? Array.from(input.files) : [];
          const labelValue = files.length > 1 ? "已选 " + files.length + " 个文件" : files[0] ? files[0].name : "选择文件";
          labelText.textContent = labelValue;
          label.title = files.map((file) => file.name).join("；");
          label.classList.toggle("selected", files.length > 0);
        });
      }
      if (queueButton) {
        queueButton.addEventListener("click", () => queueFormFiles(form, input, label, labelText));
      }
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const status = form.querySelector(".upload-status");
        const button = form.querySelector('button[type="submit"]');
        const files = input && input.files ? Array.from(input.files) : [];
        if (!validateUploadFiles(files, status)) return;
        button.disabled = true;
        const defaultButtonText = button.dataset.defaultText || button.textContent;
        button.dataset.defaultText = defaultButtonText;
        button.textContent = "审核中";
        const hasLargeFile = files.some((file) => file.size > 1024 * 1024);
        status.textContent = hasLargeFile ? "文件较大，已加入审核队列，可能需要 1-2 分钟。" : "已加入审核队列。";
        try {
          await enqueueUpload(async () => {
            const preparedFiles = [];
            for (let index = 0; index < files.length; index += 1) {
              const file = files[index];
              status.textContent = files.length > 1 ? "正在处理第 " + (index + 1) + "/" + files.length + " 个文件..." : (file.size > 1024 * 1024 ? "正在处理大文件；系统会优先压缩图片或读取 PDF/Word 文本..." : "正在上传并审核...");
              const prepared = await prepareUploadFile(file, status);
              if (prepared.size > 12 * 1024 * 1024) {
                throw new Error(prepared.fileName + " 超过 12MB，请压缩后重新上传。");
              }
              preparedFiles.push({
                file_name: prepared.fileName,
                content_type: prepared.contentType,
                data_base64: prepared.base64,
              });
            }
            const isBatch = preparedFiles.length > 1;
            status.textContent = isBatch ? "正在批量上传并审核 " + preparedFiles.length + " 个文件..." : "正在上传并审核...";
            const response = await fetch(window.location.pathname + (isBatch ? "/batch-upload" : "/upload"), {
              method: "POST",
              headers: {"Content-Type": "application/json"},
              body: JSON.stringify(isBatch ? {
                material_type: form.dataset.materialType,
                files: preparedFiles,
              } : {
                material_type: form.dataset.materialType,
                file_name: preparedFiles[0].file_name,
                content_type: preparedFiles[0].content_type,
                data_base64: preparedFiles[0].data_base64,
              }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
              throw new Error(payload.detail || "上传失败，请稍后重试。");
            }
            status.textContent = isBatch ? "本次 " + preparedFiles.length + " 个文件审核完成，请查看上方汇总。" : (payload.review_status === "auto_approved" ? "审核完成，已通过。" : "审核完成，请查看上方反馈。");
            updateCard(form, payload);
            updateNotice(payload);
            if (Array.isArray(payload.results)) {
              payload.results.forEach(updateSubmittedRecords);
            } else {
              updateSubmittedRecords(payload);
            }
            resetFileInput(input, label, labelText);
          }, status);
        } catch (error) {
          const message = error.message === "Failed to fetch" ? "上传请求未完成，可能是网络中断或服务超时，请稍后重试。" : error.message;
          status.textContent = message || "上传失败，请稍后重试。";
        } finally {
          button.disabled = false;
          button.textContent = button.dataset.defaultText || "上传并审核";
        }
      });
    });
    """
    )


def _css() -> str:
    return """
    :root {
      color-scheme: light;
      --bg: #f6f7f9;
      --ink: #20242c;
      --muted: #667085;
      --line: #d9dee7;
      --panel: #ffffff;
      --blue: #2f68d8;
      --green: #247d4d;
      --orange: #a95f00;
      --red: #c53b34;
      --cyan: #007b83;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      letter-spacing: 0;
    }
    .hero {
      min-height: 240px;
      padding: 36px 40px 32px;
      display: flex;
      align-items: flex-end;
      justify-content: space-between;
      gap: 28px;
      color: #fff;
      background:
        linear-gradient(120deg, rgba(18, 43, 76, .92), rgba(26, 89, 117, .82)),
        repeating-linear-gradient(90deg, rgba(255,255,255,.18) 0 1px, transparent 1px 48px),
        linear-gradient(135deg, #18314f, #16616c);
    }
    .candidate-hero {
      background:
        linear-gradient(120deg, rgba(37, 61, 52, .94), rgba(30, 97, 104, .82)),
        repeating-linear-gradient(90deg, rgba(255,255,255,.16) 0 1px, transparent 1px 48px),
        linear-gradient(135deg, #21483d, #12606d);
    }
    .materials-hero {
      background:
        linear-gradient(120deg, rgba(45, 50, 72, .94), rgba(28, 103, 96, .82)),
        repeating-linear-gradient(90deg, rgba(255,255,255,.15) 0 1px, transparent 1px 48px),
        linear-gradient(135deg, #2d3348, #17675f);
    }
    .eyebrow { margin: 0 0 8px; font-size: 13px; text-transform: uppercase; color: rgba(255,255,255,.72); }
    h1 { margin: 0; font-size: clamp(28px, 4vw, 44px); line-height: 1.15; font-weight: 760; }
    .lead { margin: 12px 0 0; max-width: 760px; color: rgba(255,255,255,.82); font-size: 16px; }
    .hero-metrics { display: grid; grid-template-columns: repeat(2, minmax(116px, 1fr)); gap: 10px; min-width: 280px; }
    .metric { min-height: 76px; padding: 14px; border: 1px solid rgba(255,255,255,.28); background: rgba(255,255,255,.11); }
    .metric span { display: block; color: rgba(255,255,255,.76); font-size: 13px; }
    .metric strong { display: block; margin-top: 8px; font-size: 24px; line-height: 1; }
    .candidate-hero .hero-metrics {
      grid-template-columns: repeat(3, minmax(92px, 1fr));
      min-width: min(430px, 42vw);
      gap: 8px;
    }
    .candidate-hero .metric {
      min-height: 56px;
      padding: 10px 12px;
    }
    .candidate-hero .metric span {
      font-size: 12px;
    }
    .candidate-hero .metric strong {
      margin-top: 5px;
      font-size: 20px;
    }
    .layout, .candidate-layout, .materials-layout {
      width: min(1440px, calc(100% - 48px));
      margin: 24px auto 40px;
      display: grid;
      grid-template-columns: 1.4fr .8fr;
      gap: 18px;
    }
    .candidate-layout { grid-template-columns: 1fr 1fr; }
    .materials-layout { grid-template-columns: minmax(0, 1.35fr) minmax(320px, .65fr); align-items: start; }
    .side-stack { display: grid; gap: 18px; }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }
    .wide { grid-column: 1 / -1; }
    .panel-head {
      min-height: 58px;
      padding: 16px 18px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      border-bottom: 1px solid var(--line);
      gap: 12px;
    }
    .panel-head.tall { align-items: flex-start; }
    .panel-head h2 { margin: 0; font-size: 17px; }
    .panel-subtitle { margin: 6px 0 0; color: var(--muted); font-size: 13px; line-height: 1.5; }
    .panel-head span, .muted { color: var(--muted); font-size: 13px; }
    .action-link {
      display: inline-flex;
      align-items: center;
      min-height: 30px;
      padding: 0 10px;
      border: 1px solid #b8cdf6;
      background: #eef4ff;
      color: var(--blue);
      font-size: 13px;
      font-weight: 700;
      white-space: nowrap;
    }
    .table-wrap { overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; min-width: 1120px; }
    th, td { padding: 13px 14px; border-bottom: 1px solid #edf0f5; text-align: left; font-size: 14px; white-space: nowrap; }
    th { color: var(--muted); font-weight: 650; background: #fafbfc; }
    a { color: var(--blue); text-decoration: none; font-weight: 650; }
    .candidate-cell {
      min-width: 132px;
    }
    .hr-case-form {
      display: none;
    }
    .stage-controls {
      min-width: 214px;
      display: flex;
      gap: 7px;
      align-items: center;
    }
    .hr-inline-select {
      height: 32px;
      max-width: 136px;
      border: 1px solid #cbd5e1;
      background: #fff;
      color: var(--ink);
      font-size: 13px;
    }
    .node-confirm-form button {
      min-height: 32px;
      padding: 0 11px;
      border: 1px solid #1f5fca;
      background: var(--blue);
      color: #fff;
      font-size: 13px;
      font-weight: 700;
      cursor: pointer;
      white-space: nowrap;
    }
    .hr-inline-select:disabled {
      background: #f3f5f8;
      color: #667085;
      cursor: wait;
    }
    .node-confirm-form button:disabled {
      border-color: #aeb7c5;
      background: #aeb7c5;
      cursor: wait;
    }
    .form-status {
      display: block;
      margin-top: 5px;
      min-height: 18px;
      color: var(--muted);
      font-size: 12px;
    }
    .hr-workspace-hero {
      width: min(1440px, calc(100% - 48px));
      margin: 20px auto 0;
      min-height: 150px;
      padding: 24px 28px;
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(430px, .9fr);
      gap: 22px;
      align-items: end;
      border: 1px solid #cbd8ea;
      border-radius: 8px;
      background: #173f5f;
      color: #fff;
    }
    .hr-workspace-hero h1 {
      font-size: 28px;
      line-height: 1.2;
    }
    .hr-workspace-hero p {
      max-width: 760px;
      margin: 10px 0 0;
      color: rgba(255,255,255,.82);
      font-size: 14px;
      line-height: 1.55;
    }
    .hr-workspace-metrics {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
    }
    .hr-workspace-metrics .metric {
      min-height: 66px;
      padding: 11px 12px;
    }
    .hr-workspace-metrics .metric strong {
      font-size: 20px;
    }
    .hr-workspace-layout {
      width: min(1440px, calc(100% - 48px));
      margin: 18px auto 40px;
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(360px, .48fr);
      gap: 18px;
      align-items: start;
    }
    .panel-actions {
      display: flex;
      align-items: center;
      gap: 10px;
      white-space: nowrap;
    }
    .secondary-action,
    .hr-save-row,
    .candidate-create-form button,
    .candidate-import-form button {
      min-height: 32px;
      padding: 0 12px;
      border: 1px solid #1f5fca;
      background: var(--blue);
      color: #fff;
      font-size: 13px;
      font-weight: 750;
      cursor: pointer;
    }
    .secondary-action {
      border-color: #cbd5e1;
      background: #fff;
      color: var(--blue);
    }
    .hr-save-row:disabled,
    .candidate-create-form button:disabled,
    .candidate-import-form button:disabled {
      border-color: #cbd5e1;
      background: #edf0f5;
      color: #667085;
      cursor: default;
    }
    .danger-link {
      margin: 6px 0 0;
      padding: 0;
      display: block;
      border: 0;
      background: transparent;
      color: #b42318;
      font-size: 12px;
      font-weight: 650;
      cursor: pointer;
    }
    .danger-link:disabled {
      color: #98a2b3;
      cursor: default;
    }
    .chat-action {
      min-height: 30px;
      padding: 0 10px;
      border: 1px solid #cbd5e1;
      background: #fff;
      color: var(--blue);
      font-size: 12px;
      font-weight: 700;
      cursor: pointer;
      white-space: nowrap;
    }
    .chat-action:disabled {
      color: #667085;
      background: #f3f5f8;
      cursor: default;
    }
    .chat-action-stack {
      display: grid;
      gap: 5px;
      justify-items: start;
    }
    .chat-missing-reason {
      color: #b42318;
      font-size: 12px;
      line-height: 1.35;
      white-space: nowrap;
    }
    .chat-id {
      display: inline-block;
      max-width: 150px;
      color: var(--muted);
      font-size: 12px;
      overflow: hidden;
      text-overflow: ellipsis;
      vertical-align: middle;
      white-space: nowrap;
    }
    .hr-edit-row.is-dirty {
      background: #fffdf6;
    }
    .candidate-create-form,
    .candidate-import-form {
      padding: 16px 18px;
      border-bottom: 1px solid var(--line);
      background: #fbfcff;
    }
    .candidate-create-form.is-collapsed,
    .candidate-import-form.is-collapsed {
      display: none;
    }
    .import-form-main {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 16px;
      align-items: center;
    }
    .import-form-main h3 {
      margin: 0 0 6px;
      font-size: 15px;
    }
    .import-form-main p,
    .candidate-import-form .form-status {
      margin: 0;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.5;
    }
    .import-form-actions {
      display: flex;
      align-items: center;
      gap: 10px;
      white-space: nowrap;
    }
    .import-file-control {
      width: 180px;
    }
    .candidate-import-form .form-status {
      margin-top: 10px;
    }
    .create-form-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(180px, 1fr));
      gap: 12px;
    }
    .candidate-create-form label {
      display: grid;
      gap: 6px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }
    .candidate-create-form input,
    .candidate-create-form select {
      height: 34px;
      min-width: 0;
      border: 1px solid #cbd5e1;
      background: #fff;
      color: var(--ink);
      font-size: 13px;
    }
    .create-form-footer {
      margin-top: 14px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }
    .create-form-footer p {
      margin: 0;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.5;
    }
    .create-form-footer div {
      display: flex;
      align-items: center;
      gap: 10px;
      white-space: nowrap;
    }
    .hr-line-table {
      min-width: 1400px;
    }
    .hr-line-table th,
    .hr-line-table td {
      padding: 12px 14px;
    }
    .line-cell .hr-inline-select {
      width: 138px;
      max-width: 138px;
    }
    .position-cell .hr-inline-select {
      width: 230px;
      max-width: 230px;
    }
    .hr-workspace-table-panel .stage-controls {
      min-width: 226px;
    }
    .stack { display: grid; }
    .item {
      min-height: 76px;
      padding: 14px 16px;
      display: flex;
      justify-content: space-between;
      gap: 16px;
      border-bottom: 1px solid #edf0f5;
    }
    .item:last-child { border-bottom: 0; }
    .item > div:first-child { min-width: 0; }
    .item strong { display: block; font-size: 14px; }
    .item p { margin: 6px 0 0; color: var(--muted); font-size: 13px; line-height: 1.5; overflow-wrap: anywhere; }
    .right { display: grid; justify-items: end; align-content: start; gap: 8px; min-width: 84px; }
    .pill {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 64px;
      min-height: 26px;
      padding: 0 9px;
      border: 1px solid #cfd6e2;
      background: #f7f9fc;
      color: #384152;
      font-size: 12px;
      font-weight: 650;
      white-space: nowrap;
    }
    .pill.risk, .pill.high, .pill.overdue, .pill.auto-rejected, .pill.rejected { border-color: #f0bbb7; background: #fff1f0; color: var(--red); }
    .pill.medium, .pill.manual-review, .pill.submitted, .pill.needs-supplement { border-color: #f1d0a6; background: #fff7e8; color: var(--orange); }
    .pill.completed, .pill.approved, .pill.auto-approved, .pill.low { border-color: #add9bf; background: #eef9f2; color: var(--green); }
    .pill.in-progress, .pill.pending { border-color: #b8cdf6; background: #eef4ff; color: var(--blue); }
    .pill.not-submitted { border-color: #d6dce6; background: #f7f9fc; color: #566173; }
    .material-grid { padding: 16px; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
    .material-card {
      min-height: 238px;
      padding: 16px;
      border: 1px solid #dfe4ec;
      border-radius: 8px;
      background: #fff;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .material-top { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
    .material-title { display: flex; gap: 10px; min-width: 0; }
    .material-title span {
      flex: 0 0 34px;
      height: 34px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border: 1px solid #cbd5e1;
      background: #f8fafc;
      color: #475467;
      font-size: 12px;
      font-weight: 800;
    }
    .material-title h3 { margin: 4px 0 0; font-size: 16px; line-height: 1.35; overflow-wrap: anywhere; }
    .material-desc { margin: 0; color: #3f4652; font-size: 14px; line-height: 1.55; }
    .material-meta {
      display: grid;
      gap: 4px;
      padding: 9px 10px;
      border: 1px solid #e4e8ef;
      background: #fafbfc;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }
    .rule-list { margin: 0; padding-left: 18px; color: #4b5563; font-size: 13px; line-height: 1.55; }
    .rule-list li + li { margin-top: 4px; }
    .material-result {
      margin: 0;
      padding: 9px 10px;
      border: 1px solid #d6dce6;
      background: #f7f9fc;
      color: #384152;
      font-size: 13px;
      line-height: 1.5;
    }
    .material-result strong { display: block; margin-bottom: 4px; font-size: 13px; }
    .material-result p { margin: 0; color: inherit; }
    .material-result.auto-rejected, .material-result.rejected {
      border-color: #f0bbb7;
      background: #fff1f0;
      color: var(--red);
    }
    .material-result.manual-review {
      border-color: #f1d0a6;
      background: #fff7e8;
      color: var(--orange);
    }
    .material-result.needs-supplement {
      border-color: #f1d0a6;
      background: #fff7e8;
      color: var(--orange);
    }
    .material-result.auto-approved, .material-result.approved {
      border-color: #add9bf;
      background: #eef9f2;
      color: var(--green);
    }
    .bulk-submit-panel {
      margin: 0 16px 12px;
      padding: 12px;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px 12px;
      align-items: center;
      border: 1px solid #dfe4ec;
      border-radius: 8px;
      background: #f8fafc;
    }
    .bulk-submit-copy {
      display: flex;
      align-items: baseline;
      gap: 10px;
      min-width: 0;
    }
    .bulk-submit-copy strong { color: #252b36; font-size: 14px; }
    .bulk-submit-copy span {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }
    .bulk-submit-list {
      grid-column: 1 / -1;
      color: #4b5563;
      font-size: 12px;
      line-height: 1.55;
    }
    .bulk-submit-list span + span::before {
      content: "；";
    }
    .bulk-submit-actions {
      display: flex;
      gap: 8px;
      justify-content: flex-end;
      white-space: nowrap;
    }
    .bulk-submit-actions button {
      min-height: 32px;
      padding: 0 12px;
      border: 1px solid #cbd5e1;
      background: #fff;
      color: #344054;
      font-size: 13px;
      font-weight: 700;
      cursor: pointer;
    }
    .bulk-submit-actions .bulk-submit-button {
      border-color: #1f5fca;
      background: var(--blue);
      color: #fff;
    }
    .bulk-submit-actions button:disabled {
      border-color: #d6dce6;
      background: #eef1f5;
      color: #8992a1;
      cursor: not-allowed;
    }
    .upload-form {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto auto;
      gap: 8px;
      align-items: center;
      margin-top: auto;
    }
    .file-control {
      min-width: 0;
      min-height: 34px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border: 1px solid #cbd5e1;
      background: #f8fafc;
      color: #344054;
      font-size: 13px;
      font-weight: 650;
      cursor: pointer;
    }
    .file-control.selected {
      border-color: #8fb4f4;
      background: #eef4ff;
      color: var(--blue);
    }
    .file-control span {
      max-width: 100%;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      padding: 0 8px;
    }
    .file-control input {
      width: 1px;
      height: 1px;
      opacity: 0;
      position: absolute;
      pointer-events: none;
    }
    .upload-form button {
      min-height: 34px;
      padding: 0 12px;
      border: 1px solid #1f5fca;
      background: var(--blue);
      color: #fff;
      font-size: 13px;
      font-weight: 700;
      cursor: pointer;
    }
    .upload-form .queue-upload-button {
      border-color: #cbd5e1;
      background: #fff;
      color: #344054;
    }
    .upload-form button:disabled {
      border-color: #aeb7c5;
      background: #aeb7c5;
      cursor: wait;
    }
    .upload-status {
      grid-column: 1 / -1;
      min-height: 18px;
      margin: 0;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }
    .notice-list {
      width: min(1440px, calc(100% - 48px));
      margin: 18px auto 0;
      display: grid;
      gap: 10px;
    }
    .notice {
      padding: 14px 16px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      border: 1px solid #b8cdf6;
      border-radius: 8px;
      background: #eef4ff;
    }
    .notice p { width: 100%; margin: 6px 0 0; color: var(--muted); font-size: 13px; line-height: 1.5; }
    .notice.auto-rejected, .notice.rejected { border-color: #f0bbb7; background: #fff1f0; }
    .notice.needs-supplement, .notice.submitted { border-color: #f1d0a6; background: #fff7e8; }
    .notice.auto-approved, .notice.approved { border-color: #add9bf; background: #eef9f2; }
    .submitted-actions {
      display: flex;
      align-items: center;
      gap: 8px;
      white-space: nowrap;
    }
    .submitted-count {
      color: var(--muted);
      font-size: 13px;
    }
    .history-toggle {
      min-height: 30px;
      padding: 0 10px;
      border: 1px solid #cbd5e1;
      background: #f8fafc;
      color: #344054;
      font-size: 13px;
      font-weight: 650;
      cursor: pointer;
    }
    .history-toggle[hidden] { display: none; }
    .collapsible-list,
    .submitted-records-list {
      position: relative;
      transition: max-height .18s ease;
    }
    .collapsible-list.is-collapsed,
    .submitted-records-list.is-collapsed {
      max-height: 292px;
      overflow: hidden;
    }
    .candidate-material-list.is-collapsed {
      max-height: min(292px, var(--collapsible-max-height, 292px));
    }
    .collapsible-list.is-collapsed::after,
    .submitted-records-list.is-collapsed::after {
      content: "";
      position: absolute;
      left: 0;
      right: 0;
      bottom: 0;
      height: 44px;
      pointer-events: none;
      background: linear-gradient(rgba(255,255,255,0), #fff);
    }
    .collapsible-list.is-expanded,
    .submitted-records-list.is-expanded {
      max-height: min(720px, 74vh);
      overflow-y: auto;
    }
    .workspace-submitted-records {
      overflow: hidden;
    }
    .workspace-submitted-records .submitted-records-list {
      max-height: var(--workspace-submitted-list-height, none);
      overflow-y: auto;
      transition: none;
    }
    .candidate-material-list.is-expanded {
      max-height: var(--collapsible-max-height, min(720px, 74vh));
    }
    .material-status-panel { align-self: start; }
    .material-status-panel .panel-head { align-items: flex-start; }
    .follow-list { display: grid; }
    .follow-item { padding: 14px 16px; border-bottom: 1px solid #edf0f5; }
    .follow-item:last-child { border-bottom: 0; }
    .follow-item strong { display: block; font-size: 14px; }
    .follow-item p { margin: 6px 0 0; color: var(--muted); font-size: 13px; line-height: 1.5; }
    .timeline { padding: 12px 16px 18px; display: grid; gap: 16px; }
    .timeline-group { border-left: 1px solid var(--line); padding-left: 16px; }
    .timeline-group h3 { margin: 0 0 8px; font-size: 15px; }
    .timeline-row { min-height: 50px; display: grid; grid-template-columns: 14px 1fr; gap: 10px; align-items: start; padding: 8px 0; }
    .timeline-row p { margin: 4px 0 0; color: var(--muted); font-size: 13px; }
    .node-confirm-form {
      margin-top: 8px;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .confirm-status {
      color: var(--muted);
      font-size: 12px;
    }
    .dot { width: 10px; height: 10px; margin-top: 4px; border: 2px solid #aeb7c5; background: #fff; }
    .dot.approved, .dot.skipped { border-color: var(--green); background: var(--green); }
    .dot.pending, .dot.submitted { border-color: var(--blue); background: var(--blue); }
    .dot.overdue, .dot.rejected { border-color: var(--red); background: var(--red); }
    .empty { padding: 22px 18px; color: var(--muted); font-size: 14px; }
    .workspace-hero {
      width: min(1440px, calc(100% - 48px));
      margin: 20px auto 0;
      padding: 20px;
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(420px, .82fr);
      gap: 20px;
      align-items: end;
      border-radius: 10px;
      background: linear-gradient(135deg, #153746, #1f5d62);
      color: #fff;
    }
    .workspace-kicker {
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 0 9px;
      border: 1px solid rgba(255,255,255,.32);
      color: rgba(255,255,255,.82);
      font-size: 12px;
      font-weight: 700;
    }
    .workspace-title h1 {
      margin-top: 12px;
      max-width: 760px;
      font-size: 30px;
      line-height: 1.2;
      text-wrap: balance;
    }
    .workspace-title p {
      margin: 10px 0 0;
      color: rgba(255,255,255,.82);
      font-size: 14px;
      line-height: 1.55;
    }
    .workspace-status-strip {
      min-width: 0;
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
    }
    .workspace-stat {
      min-height: 58px;
      padding: 10px 12px;
      display: grid;
      align-content: center;
      gap: 5px;
      border: 1px solid rgba(255,255,255,.26);
      background: rgba(255,255,255,.1);
    }
    .workspace-stat span {
      color: rgba(255,255,255,.76);
      font-size: 12px;
    }
    .workspace-stat strong {
      color: #fff;
      font-size: 18px;
      line-height: 1.15;
      overflow-wrap: anywhere;
    }
    .workspace-layout {
      width: min(1440px, calc(100% - 48px));
      margin: 18px auto 40px;
      display: grid;
      grid-template-columns: minmax(320px, .72fr) minmax(0, 1.28fr);
      gap: 18px;
      align-items: start;
    }
    .workspace-column {
      min-width: 0;
      display: grid;
      gap: 18px;
    }
    .workspace-panel {
      scroll-margin-top: 16px;
    }
    .workspace-actions {
      display: grid;
    }
    .workspace-action {
      min-height: 74px;
      padding: 14px 16px;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 14px;
      align-items: center;
      border-bottom: 1px solid #edf0f5;
    }
    .workspace-action:last-child {
      border-bottom: 0;
    }
    .workspace-action strong {
      display: block;
      font-size: 14px;
      line-height: 1.4;
      overflow-wrap: anywhere;
    }
    .workspace-action p {
      margin: 5px 0 0;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
      text-wrap: pretty;
    }
    .workspace-action a {
      min-height: 32px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 0 10px;
      border: 1px solid #b8cdf6;
      background: #eef4ff;
      color: var(--blue);
      font-size: 13px;
      font-weight: 750;
      white-space: nowrap;
    }
    .workspace-action.overdue a,
    .workspace-action.rejected a,
    .workspace-action.auto-rejected a {
      border-color: #f0bbb7;
      background: #fff1f0;
      color: var(--red);
    }
    .workspace-action.manual-review a,
    .workspace-action.needs-supplement a {
      border-color: #f1d0a6;
      background: #fff7e8;
      color: var(--orange);
    }
    .workspace-flow .timeline {
      padding-bottom: 14px;
    }
    .workspace-material-summary {
      display: flex;
      gap: 8px;
    }
    .workspace-material-summary .workspace-stat {
      min-width: 88px;
      min-height: 46px;
      border-color: #dfe4ec;
      background: #f8fafc;
    }
    .workspace-material-summary .workspace-stat span {
      color: var(--muted);
    }
    .workspace-material-summary .workspace-stat strong {
      color: var(--ink);
      font-size: 16px;
    }
    .workspace-progress {
      padding: 13px 16px;
      display: grid;
      grid-template-columns: auto minmax(0, 1fr);
      gap: 14px;
      align-items: center;
      border-bottom: 1px solid #edf0f5;
      background: #fbfcfe;
    }
    .workspace-progress strong {
      display: block;
      font-size: 16px;
    }
    .workspace-progress span {
      color: var(--muted);
      font-size: 12px;
    }
    .workspace-progress-track {
      height: 8px;
      overflow: hidden;
      background: #e8edf4;
    }
    .workspace-progress-track span {
      display: block;
      width: var(--progress);
      height: 100%;
      background: var(--green);
    }
    .workspace-materials .material-grid {
      grid-template-columns: repeat(auto-fit, minmax(310px, 1fr));
      padding: 14px;
    }
    .workspace-materials .material-card {
      min-height: 0;
    }
    .workspace-materials .material-title h3 {
      font-size: 15px;
    }
    .workspace-materials .material-desc,
    .workspace-materials .rule-list,
    .workspace-materials .material-result {
      font-size: 12px;
    }
    .workspace-materials .upload-form {
      grid-template-columns: minmax(0, 1fr) auto auto;
    }
    .workspace-action a:focus-visible,
    .action-link:focus-visible,
    .history-toggle:focus-visible,
    .upload-form button:focus-visible,
    .bulk-submit-actions button:focus-visible,
    .candidate-import-form button:focus-visible,
    .danger-link:focus-visible,
    .file-control:focus-within,
    .node-confirm-form button:focus-visible {
      outline: 3px solid rgba(47, 104, 216, .28);
      outline-offset: 2px;
    }
    @media (max-width: 860px) {
      .hero { padding: 28px 20px; display: block; }
      .hero-metrics { margin-top: 22px; min-width: 0; }
      .candidate-hero .hero-metrics { grid-template-columns: repeat(3, minmax(0, 1fr)); }
      .layout, .candidate-layout, .materials-layout { width: calc(100% - 24px); grid-template-columns: 1fr; }
      .material-grid { grid-template-columns: 1fr; padding: 12px; }
      .bulk-submit-panel {
        margin: 0 12px 12px;
        grid-template-columns: 1fr;
      }
      .bulk-submit-copy,
      .bulk-submit-actions {
        display: grid;
        grid-template-columns: 1fr;
        justify-content: stretch;
      }
      .bulk-submit-actions button,
      .upload-form button,
      .file-control {
        width: 100%;
      }
      .upload-form {
        grid-template-columns: 1fr;
      }
      .notice-list { width: calc(100% - 24px); }
      .notice { display: grid; }
      .wide { grid-column: auto; }
      .hr-workspace-hero,
      .hr-workspace-layout {
        width: calc(100% - 24px);
        grid-template-columns: 1fr;
      }
      .hr-workspace-hero {
        margin-top: 12px;
        padding: 18px;
      }
      .hr-workspace-hero h1 {
        font-size: 22px;
      }
      .hr-workspace-metrics {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }
      .create-form-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }
      .create-form-footer {
        align-items: flex-start;
        flex-direction: column;
      }
      .import-form-main,
      .import-form-actions {
        display: grid;
        grid-template-columns: 1fr;
      }
      .import-file-control {
        width: 100%;
      }
      .workspace-hero {
        width: calc(100% - 24px);
        margin-top: 12px;
        grid-template-columns: 1fr;
        padding: 14px;
      }
      .workspace-title h1 {
        font-size: 22px;
      }
      .workspace-title p { font-size: 13px; }
      .workspace-title,
      .workspace-status-strip {
        min-width: 0;
      }
      .workspace-status-strip {
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 4px;
      }
      .workspace-stat {
        min-height: 48px;
        min-width: 0;
        padding: 7px 5px;
      }
      .workspace-stat span {
        font-size: 11px;
      }
      .workspace-stat strong {
        font-size: 14px;
        white-space: nowrap;
      }
      .workspace-layout {
        width: calc(100% - 24px);
        grid-template-columns: 1fr;
      }
      .workspace-submitted-records {
        overflow: hidden;
      }
      .workspace-submitted-records .submitted-records-list {
        max-height: var(--workspace-submitted-list-height, min(360px, 52vh));
        overflow-y: auto;
        -webkit-overflow-scrolling: touch;
        border-top: 1px solid #edf0f5;
      }
      .workspace-submitted-records .item {
        display: grid;
        grid-template-columns: minmax(0, 1fr);
        gap: 10px;
      }
      .workspace-submitted-records .right {
        min-width: 0;
        justify-items: start;
      }
      .workspace-action {
        grid-template-columns: 1fr;
        gap: 10px;
      }
      .workspace-action a {
        width: 100%;
      }
      .workspace-material-summary {
        width: 100%;
      }
      .workspace-material-summary .workspace-stat {
        flex: 1 1 0;
      }
      .workspace-progress {
        grid-template-columns: 1fr;
      }
      .workspace-materials .material-grid {
        grid-template-columns: 1fr;
        padding: 12px;
      }
      .workspace-materials .material-card {
        min-width: 0;
        padding: 14px;
      }
      .workspace-materials .material-top {
        display: grid;
        grid-template-columns: minmax(0, 1fr);
        gap: 8px;
      }
      .workspace-materials .material-top .pill {
        justify-self: start;
      }
      .workspace-materials .material-title,
      .workspace-materials .material-title h3,
      .workspace-materials .material-meta span,
      .workspace-materials .rule-list li,
      .workspace-materials .material-result p,
      .workspace-materials .upload-status {
        min-width: 0;
        overflow-wrap: anywhere;
        word-break: break-word;
      }
      .workspace-materials .upload-form {
        grid-template-columns: 1fr;
      }
      .workspace-materials .file-control,
      .workspace-materials .upload-form button {
        width: 100%;
      }
    }
    """
