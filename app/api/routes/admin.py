from __future__ import annotations

import base64
import binascii
import logging
import json
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session, selectinload

from app.adapters.feishu_bitable import (
    batch_create_records,
    candidate_case_to_bitable_fields,
    create_record,
    delete_record,
    material_to_bitable_fields,
    update_record,
    workflow_node_to_bitable_fields,
)
from app.adapters.feishu_im import material_review_result_card, send_interactive_card, send_text, welcome_text
from app.core.config import get_settings
from app.core.security import require_hr_access
from app.db.session import get_db
from app.models.onboarding import CandidateCase, MaterialSubmission
from app.schemas.onboarding import (
    CandidateCaseCreate,
    CandidateCaseRead,
    CaseProgress,
    MaterialReviewRequest,
    MaterialReviewFileRequest,
    MaterialReviewFileResponse,
    MaterialReviewResult,
    QARequest,
    QAResponse,
)
from app.services.gemini_ocr import extract_material_with_gemini
from app.services.gemini_material_judge import judge_material_with_gemini
from app.services.candidate_import import parse_candidate_import_xlsx
from app.services.material_submission_flow import sync_material_to_bitable as sync_material_submission_to_bitable
from app.services.material_review import review_material
from app.services.onboarding_chat import ensure_onboarding_chat
from app.services.onboarding_workflow import DuplicateCandidateError, create_candidate_case, get_progress
from app.services.policy_table import list_policy_coefficient_rows
from app.services.qa_service import answer_hr_question
from app.services.reminder_service import scan_overdue_and_notify
from app.utils.feishu_links import candidate_materials_url, candidate_page_url

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_hr_access)])


class CandidateExcelImportRequest(BaseModel):
    file_name: str
    data_base64: str


class CreateOnboardingChatRequest(BaseModel):
    force_add_members: bool = False


@router.post("/cases")
async def create_case(payload: CandidateCaseCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Create a candidate case locally and sync to Feishu Bitable when configured."""

    try:
        case = create_candidate_case(db, payload)
    except DuplicateCandidateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    sync_result = await _sync_case_to_bitable(case, db)
    welcome_result = await _notify_candidate_welcome(case)
    return {"case": CandidateCaseRead.model_validate(case), "bitable_sync": sync_result, "welcome_message": welcome_result}


@router.get("/cases", response_model=list[CandidateCaseRead])
def list_cases(db: Session = Depends(get_db)) -> list[CandidateCase]:
    """List local candidate cases."""

    return db.query(CandidateCase).order_by(CandidateCase.created_at.desc()).all()


@router.get("/policies/social-benefits")
def list_social_benefit_policies(city: Optional[str] = None) -> dict[str, Any]:
    """Return structured social insurance and housing fund policy rows."""

    rows = [row.to_dict() for row in list_policy_coefficient_rows(city=city)]
    return {"rows": rows, "row_count": len(rows), "source_index": "examples/knowledge_base/demo_policy.md"}


@router.post("/cases/import-excel")
async def import_cases_from_excel(payload: CandidateExcelImportRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Import candidate cases from a fixed-column Excel template."""

    file_bytes = _decode_excel_data(payload)
    try:
        parsed = await parse_candidate_import_xlsx(file_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    errors = list(parsed.errors)
    for row in parsed.rows:
        try:
            case = create_candidate_case(db, row.payload)
        except DuplicateCandidateError as exc:
            skipped.append({"row": row.row_number, "status": "duplicate", "message": str(exc)})
            continue
        except Exception as exc:
            errors.append({"row": row.row_number, "message": str(exc)})
            continue
        sync_result = await _sync_case_to_bitable(case, db)
        welcome_result = await _notify_candidate_welcome(case)
        created.append(
            {
                "row": row.row_number,
                "case": CandidateCaseRead.model_validate(case),
                "warnings": row.warnings,
                "bitable_sync": sync_result,
                "welcome_message": welcome_result,
            }
        )

    return {
        "status": "success",
        "created_count": len(created),
        "skipped_count": len(skipped),
        "error_count": len(errors),
        "created": created,
        "skipped": skipped,
        "errors": errors,
    }


@router.delete("/cases/{case_id}")
async def delete_case(case_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Delete a local candidate case and try to remove linked Feishu Bitable records."""

    case = (
        db.query(CandidateCase)
        .options(selectinload(CandidateCase.nodes), selectinload(CandidateCase.materials))
        .filter(CandidateCase.id == case_id)
        .one_or_none()
    )
    if not case:
        raise HTTPException(status_code=404, detail="case not found")

    bitable_delete = await _delete_case_from_bitable(case)
    candidate_name = case.candidate_name
    db.delete(case)
    db.commit()
    return {
        "status": "success",
        "message": f"已删除 {candidate_name}，并已尝试同步删除飞书多维表记录。",
        "bitable_delete": bitable_delete,
    }


@router.post("/cases/{case_id}/feishu-chat")
async def create_case_feishu_chat(
    case_id: int,
    payload: Optional[CreateOnboardingChatRequest] = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Create or update the Feishu onboarding chat for a candidate case."""

    case = db.query(CandidateCase).filter(CandidateCase.id == case_id).one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="case not found")
    try:
        return await ensure_onboarding_chat(case, db, force_add_members=bool(payload and payload.force_add_members))
    except Exception as exc:
        logger.warning("create_onboarding_chat_failed case_id=%s error_type=%s", case_id, type(exc).__name__)
        return {"status": "failed", "reason": type(exc).__name__}


@router.get("/cases/{case_id}/progress", response_model=CaseProgress)
def get_case_progress(case_id: int, db: Session = Depends(get_db)) -> CaseProgress:
    """Return onboarding progress for a case."""

    progress = get_progress(db, case_id)
    if not progress:
        raise HTTPException(status_code=404, detail="case not found")
    return progress


@router.post("/materials/review", response_model=MaterialReviewResult)
async def review_material_endpoint(payload: MaterialReviewRequest) -> MaterialReviewResult:
    """Run local material review rules for debugging."""

    review = review_material(
        material_type=payload.material_type,
        candidate_name=payload.candidate_name,
        expected_onboard_date=payload.expected_onboard_date,
        ocr_text=payload.ocr_text,
        mock_extracted_fields=payload.mock_extracted_fields,
    )
    if not payload.use_gemini_judgement:
        return review
    return await judge_material_with_gemini(
        material_type=payload.material_type,
        candidate_name=payload.candidate_name,
        expected_onboard_date=payload.expected_onboard_date,
        ocr_text=payload.ocr_text,
        extracted_fields=review.extracted_fields,
        base_review=review,
    )


@router.post("/materials/review-file", response_model=MaterialReviewFileResponse)
async def review_material_file_endpoint(payload: MaterialReviewFileRequest, db: Session = Depends(get_db)) -> MaterialReviewFileResponse:
    """OCR an authenticated upload-root file and review it."""

    file_path = _resolve_uploaded_file(payload.file_path)
    extracted = await extract_material_with_gemini(str(file_path), payload.material_type)
    fields = extracted.get("extracted_fields") or {}
    ocr_text = str(extracted.get("ocr_text") or "")
    quality_notes = [str(item) for item in (extracted.get("quality_notes") or [])]
    base_review = review_material(
        material_type=payload.material_type,
        candidate_name=payload.candidate_name,
        expected_onboard_date=payload.expected_onboard_date,
        ocr_text=ocr_text,
        mock_extracted_fields=fields,
    )
    review = base_review
    if payload.use_gemini_judgement:
        review = await judge_material_with_gemini(
            material_type=payload.material_type,
            candidate_name=payload.candidate_name,
            expected_onboard_date=payload.expected_onboard_date,
            ocr_text=ocr_text,
            extracted_fields=fields,
            base_review=base_review,
        )

    submission_id: int | None = None
    sync_result: dict[str, Any] = {}
    candidate_notify: dict[str, Any] = {}
    if payload.case_id:
        case = db.query(CandidateCase).filter(CandidateCase.id == payload.case_id).one_or_none()
        if not case:
            raise HTTPException(status_code=404, detail="case not found")
        material = MaterialSubmission(
            case_id=payload.case_id,
            material_type=payload.material_type,
            file_token=payload.file_token,
            file_name=payload.file_name or file_path.name,
            ocr_text=ocr_text if (get_settings().store_raw_ocr_text or get_settings().demo_mode) else None,
            extracted_fields_json=json.dumps(review.extracted_fields, ensure_ascii=False),
            review_status=review.decision,
            review_reason="；".join(f"{check.rule}:{check.reason}" for check in review.checks if check.status != "pass")[:1000],
        )
        db.add(material)
        db.commit()
        db.refresh(material)
        submission_id = material.id
        sync_result = await _sync_material_to_bitable(material, db)
        candidate_notify = await _notify_candidate_material_result(case, review)

    return MaterialReviewFileResponse(
        review=review,
        submission_id=submission_id,
        ocr_text=ocr_text,
        quality_notes=quality_notes,
        bitable_sync=sync_result,
        candidate_notify=candidate_notify,
    )


def _resolve_uploaded_file(file_path: str) -> Path:
    """Confine server-side review to files already placed under UPLOAD_ROOT."""

    upload_root = Path(get_settings().upload_root).resolve()
    candidate = Path(file_path)
    if not candidate.is_absolute():
        candidate = upload_root / candidate
    resolved = candidate.resolve()
    if not resolved.is_relative_to(upload_root) or not resolved.is_file():
        raise HTTPException(status_code=400, detail="file_path must reference an existing upload-root file")
    if resolved.stat().st_size > get_settings().max_upload_bytes:
        raise HTTPException(status_code=400, detail="file exceeds configured upload limit")
    return resolved


@router.post("/qa/ask", response_model=QAResponse)
async def ask_qa(payload: QARequest) -> QAResponse:
    """Run the local RAG + LLM HR question answering pipeline."""

    result = await answer_hr_question(payload.question)
    return QAResponse(answer=result.answer, sources=result.sources, needs_handoff=result.needs_handoff)


@router.post("/sync/case-to-bitable/{case_id}")
async def sync_case_to_bitable(case_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Sync an existing local case to the Feishu candidate table."""

    case = (
        db.query(CandidateCase)
        .options(selectinload(CandidateCase.nodes))
        .filter(CandidateCase.id == case_id)
        .one_or_none()
    )
    if not case:
        raise HTTPException(status_code=404, detail="case not found")
    return await _sync_case_to_bitable(case, db)


@router.post("/sync/materials-to-bitable/{case_id}")
async def sync_materials_to_bitable(case_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Sync the latest material submissions for a local case to the Feishu material table."""

    case = (
        db.query(CandidateCase)
        .options(selectinload(CandidateCase.materials))
        .filter(CandidateCase.id == case_id)
        .one_or_none()
    )
    if not case:
        raise HTTPException(status_code=404, detail="case not found")

    latest_by_type: dict[str, MaterialSubmission] = {}
    for material in sorted(case.materials, key=lambda item: (item.created_at, item.id or 0)):
        latest_by_type[material.material_type] = material

    results = []
    for material in latest_by_type.values():
        sync_result = await sync_material_submission_to_bitable(material, db)
        results.append(
            {
                "material_id": material.id,
                "material_type": material.material_type,
                "review_status": material.review_status,
                "bitable_sync": sync_result,
            }
        )
    return {
        "case_id": case_id,
        "materials": len(results),
        "synced": sum(1 for item in results if item["bitable_sync"].get("status") == "success"),
        "results": results,
    }


@router.post("/scan-overdue")
async def scan_overdue(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Manually scan overdue workflow nodes, sync status, and send owner reminders."""

    return await scan_overdue_and_notify(db)


async def _notify_candidate_material_result(case: CandidateCase, review: MaterialReviewResult) -> dict[str, Any]:
    if not case.feishu_open_id:
        return {"status": "skipped", "reason": "candidate feishu_open_id not configured"}
    failed_checks = [f"{check.rule}：{check.reason}" for check in review.checks if check.status != "pass"]
    card = material_review_result_card(
        candidate_name=case.candidate_name,
        material_type=review.material_type,
        decision=review.decision,
        message_to_candidate=review.message_to_candidate,
        failed_checks=failed_checks,
        action_url=candidate_page_url(case.id),
    )
    try:
        result = await send_interactive_card(case.feishu_open_id, card)
        return {"status": "success", "result": result}
    except Exception as exc:
        logger.warning(
            "candidate_material_notify_failed case_id=%s material_type=%s error_type=%s",
            case.id,
            review.material_type,
            type(exc).__name__,
        )
        return {"status": "failed", "reason": type(exc).__name__}


async def _notify_candidate_welcome(case: CandidateCase) -> dict[str, Any]:
    if not case.feishu_open_id:
        return {"status": "skipped", "reason": "candidate feishu_open_id not configured"}
    text = welcome_text(
        case.candidate_name,
        str(case.expected_onboard_date or "-"),
        detail_url=candidate_page_url(case.id),
        material_url=candidate_materials_url(case.id),
    )
    try:
        result = await send_text(case.feishu_open_id, text)
        return {"status": "success", "result": result}
    except Exception as exc:
        logger.warning("candidate_welcome_notify_failed case_id=%s error_type=%s", case.id, type(exc).__name__)
        return {"status": "failed", "reason": type(exc).__name__}


async def _sync_case_to_bitable(case: CandidateCase, db: Session | None = None) -> dict[str, Any]:
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
            result["nodes"] = await _sync_nodes_to_bitable(case, settings.feishu_base_app_token, settings.feishu_node_table_id)
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


async def _sync_nodes_to_bitable(case: CandidateCase, app_token: str, table_id: str) -> dict[str, Any]:
    created_nodes = [node for node in case.nodes if not node.feishu_bitable_record_id]
    updated_nodes = [node for node in case.nodes if node.feishu_bitable_record_id]
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


async def _sync_material_to_bitable(material: MaterialSubmission, db: Session | None = None) -> dict[str, Any]:
    settings = get_settings()
    if not settings.feishu_base_app_token or not settings.feishu_material_table_id:
        return {"status": "skipped", "reason": "FEISHU_MATERIAL_TABLE_ID not configured"}
    try:
        fields = material_to_bitable_fields(material)
        if material.feishu_bitable_record_id:
            result = await update_record(
                settings.feishu_base_app_token,
                settings.feishu_material_table_id,
                material.feishu_bitable_record_id,
                fields,
            )
            action = "updated"
        else:
            result = await create_record(settings.feishu_base_app_token, settings.feishu_material_table_id, fields)
            material.feishu_bitable_record_id = _extract_record_id(result)
            action = "created"
        if db:
            db.commit()
        return {"status": "success", "action": action, "result": result}
    except Exception as exc:
        if db:
            db.rollback()
        logger.warning("bitable_material_sync_failed material_id=%s error_type=%s", material.id, type(exc).__name__)
        return {"status": "failed", "reason": type(exc).__name__}


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


def _decode_excel_data(payload: CandidateExcelImportRequest) -> bytes:
    if not payload.file_name.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="请上传 .xlsx 模板文件")
    try:
        file_bytes = base64.b64decode(payload.data_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="invalid file data") from exc
    if not file_bytes:
        raise HTTPException(status_code=400, detail="empty file")
    if len(file_bytes) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="file exceeds 5MB limit")
    return file_bytes


async def _delete_case_from_bitable(case: CandidateCase) -> dict[str, Any]:
    settings = get_settings()
    result: dict[str, Any] = {"candidate": None, "nodes": [], "materials": []}
    if not settings.feishu_base_app_token:
        return {"status": "skipped", "reason": "FEISHU_BASE_APP_TOKEN not configured"}

    if settings.feishu_material_table_id:
        for material in case.materials:
            if material.feishu_bitable_record_id:
                result["materials"].append(
                    await _delete_bitable_record(
                        settings.feishu_base_app_token,
                        settings.feishu_material_table_id,
                        material.feishu_bitable_record_id,
                    )
                )
    if settings.feishu_node_table_id:
        for node in case.nodes:
            if node.feishu_bitable_record_id:
                result["nodes"].append(
                    await _delete_bitable_record(
                        settings.feishu_base_app_token,
                        settings.feishu_node_table_id,
                        node.feishu_bitable_record_id,
                    )
                )
    if settings.feishu_candidate_table_id and case.feishu_bitable_record_id:
        result["candidate"] = await _delete_bitable_record(
            settings.feishu_base_app_token,
            settings.feishu_candidate_table_id,
            case.feishu_bitable_record_id,
        )

    failed_count = sum(
        1
        for item in [result["candidate"], *result["nodes"], *result["materials"]]
        if isinstance(item, dict) and item.get("status") == "failed"
    )
    result["status"] = "failed" if failed_count else "success"
    return result


async def _delete_bitable_record(app_token: str, table_id: str, record_id: str) -> dict[str, Any]:
    try:
        response = await delete_record(app_token, table_id, record_id)
        return {"status": "success", "record_id": record_id, "response": response}
    except Exception as exc:
        logger.warning("bitable_record_delete_failed error_type=%s", type(exc).__name__)
        return {"status": "failed", "record_id": record_id, "reason": type(exc).__name__}
