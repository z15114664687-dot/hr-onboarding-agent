from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import re
import zipfile
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from defusedxml import ElementTree
from sqlalchemy.orm import Session

from app.adapters.feishu_bitable import (
    candidate_case_to_bitable_fields,
    create_record,
    delete_record,
    material_to_bitable_fields,
    update_record,
)
from app.adapters.feishu_im import (
    material_review_overall_summary_card,
    material_review_result_card,
    material_review_summary_card,
    send_interactive_card,
)
from app.core.config import get_settings
from app.models.onboarding import CandidateCase, MaterialSubmission
from app.providers.base import OCRRequest
from app.providers.registry import get_ocr_provider
from app.schemas.onboarding import MaterialCheck, MaterialReviewResult
from app.services.ark_material_ocr import (
    compare_material_requirements_with_ark,
    extract_material_with_ark_content,
    is_ark_material_ocr_available,
)
from app.services.material_field_summary import normalize_extracted_fields
from app.services.material_review import review_material
from app.services.material_standards import get_material_standard
from app.utils.feishu_links import candidate_materials_url

logger = logging.getLogger(__name__)
MATERIAL_REVIEW_TIMEOUT_SECONDS = 180.0
LOCAL_TEXT_MAX_CHARS = 6000
MATERIAL_BITABLE_SYNC_STATUSES = {"manual_review", "auto_approved", "approved"}
MULTI_FILE_MATERIAL_TYPES = {
    "degree_certificate",
    "employment_agreement",
    "award_certificate",
    "professional_certificate",
    "hukou_material",
}
SUPPORTING_RESUME_REQUIRED_TYPES = {"degree_certificate", "award_certificate", "professional_certificate"}
AI_CONTEXT_COMPARE_TYPES = {"degree_certificate", "award_certificate", "professional_certificate"}


async def review_and_record_material_submission(
    *,
    db: Session,
    case: CandidateCase,
    material_type: str,
    file_path: str,
    file_name: str,
    file_token: str | None = None,
    notify_candidate: bool = True,
    sync_candidate: bool = True,
) -> dict[str, Any]:
    """Review an uploaded onboarding material, persist it, sync to Bitable, and notify candidate."""

    try:
        review, ocr_text, quality_notes = await asyncio.wait_for(
            _review_uploaded_file(case, material_type, file_path, file_name),
            timeout=MATERIAL_REVIEW_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning("material_upload_review_timeout case_id=%s material_type=%s", case.id, material_type)
        review = _manual_review(
            material_type,
            "材料已收到，自动审核耗时较长，已转由 HR 人工审核。",
            "自动审核超时，已转人工复核",
        )
        ocr_text = ""
        quality_notes = [f"自动审核超过 {MATERIAL_REVIEW_TIMEOUT_SECONDS:.0f} 秒，已转人工复核。"]
    material = MaterialSubmission(
        case_id=case.id,
        material_type=material_type,
        file_token=file_token,
        file_name=file_name,
        ocr_text=ocr_text if (get_settings().store_raw_ocr_text or get_settings().demo_mode) else None,
        extracted_fields_json=json.dumps(review.extracted_fields, ensure_ascii=False),
        review_status=review.decision,
        review_reason=_review_reason(review),
    )
    db.add(material)
    db.commit()
    db.refresh(material)

    db.expire(case, ["materials"])
    sync_result = await sync_material_to_bitable(material, db)
    candidate_sync_result = (
        await sync_candidate_material_summary_to_bitable(case)
        if sync_candidate
        else {"status": "skipped", "reason": "batch candidate summary sync pending"}
    )
    notify_result = await notify_candidate_material_result(case, review) if notify_candidate else {"status": "skipped", "reason": "batch summary notification pending"}
    return {
        "material": material,
        "review": review,
        "quality_notes": quality_notes,
        "bitable_sync": sync_result,
        "candidate_sync": candidate_sync_result,
        "candidate_notify": notify_result,
    }


async def sync_material_to_bitable(material: MaterialSubmission, db: Session | None = None) -> dict[str, Any]:
    """Create or update the material record in Feishu Bitable when configured."""

    settings = get_settings()
    if not settings.feishu_base_app_token or not settings.feishu_material_table_id:
        return {"status": "skipped", "reason": "FEISHU_MATERIAL_TABLE_ID not configured"}
    try:
        cleanup_result = await _delete_superseded_material_records(material, db)
        if not _should_sync_material_record(material):
            current_delete_result = await _delete_current_material_record(material, db)
            return {
                "status": "skipped",
                "reason": "material status not synced to material table",
                "cleanup": cleanup_result,
                "current_record_cleanup": current_delete_result,
            }

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
        return {"status": "success", "action": action, "result": result, "cleanup": cleanup_result}
    except Exception as exc:
        logger.warning("bitable_material_sync_failed material_id=%s error_type=%s", material.id, type(exc).__name__)
        return {"status": "failed", "reason": type(exc).__name__}


async def _delete_superseded_material_records(
    material: MaterialSubmission,
    db: Session | None,
) -> dict[str, Any]:
    if not db or not material.id:
        return {"status": "skipped", "reason": "database session or material id not available"}
    if material.material_type in MULTI_FILE_MATERIAL_TYPES:
        return {"status": "skipped", "reason": "material type keeps multiple submissions"}

    previous = (
        db.query(MaterialSubmission)
        .filter(
            MaterialSubmission.case_id == material.case_id,
            MaterialSubmission.material_type == material.material_type,
            MaterialSubmission.id != material.id,
            MaterialSubmission.feishu_bitable_record_id.isnot(None),
        )
        .all()
    )
    if not previous:
        return {"status": "skipped", "reason": "no superseded material records"}

    deleted = 0
    failed: list[str] = []
    for item in previous:
        record_id = item.feishu_bitable_record_id
        if not record_id:
            continue
        result = await _delete_bitable_record(record_id)
        if result["status"] == "success":
            item.feishu_bitable_record_id = None
            deleted += 1
        else:
            failed.append(f"{record_id}: {result['reason']}")
    if db:
        db.commit()
    status = "success" if not failed else "partial_failed"
    return {"status": status, "deleted": deleted, "failed": failed}


async def _delete_current_material_record(material: MaterialSubmission, db: Session | None) -> dict[str, Any]:
    if not material.feishu_bitable_record_id:
        return {"status": "skipped", "reason": "current material has no bitable record"}
    result = await _delete_bitable_record(material.feishu_bitable_record_id)
    if result["status"] == "success":
        material.feishu_bitable_record_id = None
        if db:
            db.commit()
    return result


async def _delete_bitable_record(record_id: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        result = await delete_record(settings.feishu_base_app_token, settings.feishu_material_table_id, record_id)
        return {"status": "success", "result": result}
    except Exception as exc:
        logger.warning("bitable_material_delete_failed error_type=%s", type(exc).__name__)
        return {"status": "failed", "reason": type(exc).__name__}


def _should_sync_material_record(material: MaterialSubmission) -> bool:
    if material.review_status in MATERIAL_BITABLE_SYNC_STATUSES:
        return True
    return material.review_status in {"auto_rejected", "rejected"} and _is_requirement_supplement_reason(material.review_reason or "")


async def sync_candidate_material_summary_to_bitable(case: CandidateCase) -> dict[str, Any]:
    """Refresh candidate-level material progress in the Feishu main table."""

    settings = get_settings()
    if not settings.feishu_base_app_token or not settings.feishu_candidate_table_id:
        return {"status": "skipped", "reason": "FEISHU_CANDIDATE_TABLE_ID not configured"}
    if not case.feishu_bitable_record_id:
        return {"status": "skipped", "reason": "candidate bitable record id not configured"}
    try:
        result = await update_record(
            settings.feishu_base_app_token,
            settings.feishu_candidate_table_id,
            case.feishu_bitable_record_id,
            candidate_case_to_bitable_fields(case),
        )
        return {"status": "success", "action": "updated", "result": result}
    except Exception as exc:
        logger.warning(
            "bitable_candidate_material_summary_sync_failed case_id=%s error_type=%s",
            case.id,
            type(exc).__name__,
        )
        return {"status": "failed", "reason": type(exc).__name__}


async def notify_candidate_material_result(case: CandidateCase, review: MaterialReviewResult) -> dict[str, Any]:
    """Send the candidate a Feishu card with material review result when open_id is bound."""

    if not case.feishu_open_id:
        return {"status": "skipped", "reason": "candidate feishu_open_id not configured"}
    failed_checks = [f"{check.rule}：{check.reason}" for check in review.checks if check.status != "pass"]
    card = material_review_result_card(
        candidate_name=case.candidate_name,
        material_type=review.material_type,
        decision=review.decision,
        message_to_candidate=review.message_to_candidate,
        failed_checks=failed_checks,
        action_url=candidate_materials_url(case.id),
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


async def notify_candidate_material_batch_summary(
    case: CandidateCase,
    *,
    material_type: str,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Send one candidate-facing Feishu card summarizing a batch material upload."""

    if not case.feishu_open_id:
        return {"status": "skipped", "reason": "candidate feishu_open_id not configured"}
    summary_items = []
    for item in results:
        material = item.get("material")
        review = item.get("review")
        if not material or not review:
            continue
        summary_items.append(
            {
                "file_name": str(material.file_name or ""),
                "decision": str(review.decision),
                "message": str(material.review_reason or review.message_to_candidate or ""),
            }
        )
    if not summary_items:
        return {"status": "skipped", "reason": "no material results to summarize"}
    card = material_review_summary_card(
        candidate_name=case.candidate_name,
        material_type=material_type,
        results=summary_items,
        action_url=candidate_materials_url(case.id),
    )
    try:
        result = await send_interactive_card(case.feishu_open_id, card)
        return {"status": "success", "result": result}
    except Exception as exc:
        logger.warning(
            "candidate_material_batch_notify_failed case_id=%s material_type=%s error_type=%s",
            case.id,
            material_type,
            type(exc).__name__,
        )
        return {"status": "failed", "reason": type(exc).__name__}


async def notify_candidate_material_overall_summary(
    case: CandidateCase,
    *,
    groups: list[dict[str, Any]],
) -> dict[str, Any]:
    """Send one candidate-facing Feishu card summarizing a multi-material upload."""

    if not case.feishu_open_id:
        return {"status": "skipped", "reason": "candidate feishu_open_id not configured"}
    card_groups: list[dict[str, Any]] = []
    for group in groups:
        summary_items = []
        for item in group.get("results") or []:
            material = item.get("material")
            review = item.get("review")
            if not material or not review:
                continue
            summary_items.append(
                {
                    "file_name": str(material.file_name or ""),
                    "decision": str(review.decision),
                    "message": str(material.review_reason or review.message_to_candidate or ""),
                }
            )
        if summary_items:
            card_groups.append(
                {
                    "material_type": str(group.get("material_type") or ""),
                    "material_name": str(group.get("material_name") or group.get("material_type") or ""),
                    "results": summary_items,
                }
            )
    if not card_groups:
        return {"status": "skipped", "reason": "no material results to summarize"}
    card = material_review_overall_summary_card(
        candidate_name=case.candidate_name,
        groups=card_groups,
        action_url=candidate_materials_url(case.id),
    )
    try:
        result = await send_interactive_card(case.feishu_open_id, card)
        return {"status": "success", "result": result}
    except Exception as exc:
        logger.warning("candidate_material_overall_notify_failed case_id=%s error_type=%s", case.id, type(exc).__name__)
        return {"status": "failed", "reason": type(exc).__name__}


async def _review_uploaded_file(
    case: CandidateCase,
    material_type: str,
    file_path: str,
    file_name: str,
) -> tuple[MaterialReviewResult, str, list[str]]:
    extension_review = _file_extension_review(material_type, file_name)
    if extension_review:
        return extension_review, "", []

    local_precheck = _local_text_precheck_review(case, material_type, file_path, file_name)
    if local_precheck:
        return local_precheck

    settings = get_settings()
    if settings.ocr_provider in {"mock", "openai", "gemini"}:
        try:
            path = Path(file_path).resolve()
            provider_result = await get_ocr_provider().extract(
                OCRRequest(
                    file_name=file_name,
                    content=path.read_bytes(),
                    mime_type=mimetypes.guess_type(file_name)[0] or "application/octet-stream",
                    material_type=material_type,
                )
            )
            return await _review_extracted_material(
                case,
                material_type,
                file_path,
                file_name,
                {
                    "ocr_text": provider_result.ocr_text,
                    "extracted_fields": provider_result.extracted_fields,
                    "quality_notes": list(provider_result.quality_notes),
                },
                review_source=f"rules+{provider_result.provider}_ocr",
            )
        except Exception as exc:
            logger.warning("configured_ocr_provider_failed material_type=%s error_type=%s", material_type, type(exc).__name__)
            return _manual_review(
                material_type,
                "材料已收到，但自动识别暂时失败，将由 HR 人工审核。",
                "配置的 OCR provider 暂时不可用，已转人工复核",
            ), "", [type(exc).__name__]

    if not is_ark_material_ocr_available():
        fallback = _fallback_review_after_ocr_failure(case, material_type, file_path, file_name)
        if fallback:
            review, ocr_text, quality_notes = fallback
            return review, ocr_text, ["自动识别未启用，已使用本地文本兜底。", *quality_notes]
        return _manual_review(
            material_type,
            "材料已收到，自动识别暂时不可用，将由 HR 人工审核。",
            "自动识别未启用",
        ), "", []

    try:
        extracted, _ocr_source = await _extract_uploaded_material(file_path, material_type)
    except Exception as exc:
        logger.warning(
            "material_upload_ocr_failed case_id=%s material_type=%s error_type=%s",
            case.id,
            material_type,
            type(exc).__name__,
        )
        fallback = _fallback_review_after_ocr_failure(case, material_type, file_path, file_name)
        if fallback:
            review, ocr_text, quality_notes = fallback
            return review, ocr_text, [f"Primary OCR failed; used local fallback: {type(exc).__name__}", *quality_notes]
        return _manual_review(
            material_type,
            "材料已收到，但自动识别暂时失败，将由 HR 人工审核。",
            "自动识别服务暂时不可用，已转人工复核",
        ), "", [type(exc).__name__]

    return await _review_extracted_material(
        case,
        material_type,
        file_path,
        file_name,
        extracted,
        review_source="rules+ark_ocr",
    )


async def _extract_uploaded_material(file_path: str, material_type: str) -> tuple[dict[str, Any], str]:
    if not is_ark_material_ocr_available():
        raise RuntimeError("Ark OCR is not configured")
    if not _ark_supports_file(file_path):
        raise RuntimeError("Ark OCR only supports image and PDF files")
    try:
        return await _extract_material_with_ark_file(file_path, material_type), "ark"
    except Exception as exc:
        logger.warning("ark_material_ocr_failed material_type=%s error_type=%s", material_type, type(exc).__name__)
        raise RuntimeError(f"Ark OCR failed: {type(exc).__name__}") from exc


async def _extract_material_with_ark_file(file_path: str, material_type: str) -> dict[str, Any]:
    path = Path(file_path).expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"file not found: {path}")
    mime_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return await extract_material_with_ark_content(
        file_name=path.name,
        content=path.read_bytes(),
        mime_type=mime_type,
        material_type=material_type,
    )


def _ark_supports_file(file_path: str) -> bool:
    mime_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    return mime_type.startswith("image/") or mime_type == "application/pdf"


async def _review_extracted_material(
    case: CandidateCase,
    material_type: str,
    file_path: str,
    file_name: str,
    extracted: dict[str, Any],
    *,
    review_source: str | None = None,
    use_gemini_judgement: bool = True,
    quality_prefix: str | None = None,
) -> tuple[MaterialReviewResult, str, list[str]]:
    fields = extracted.get("extracted_fields") or {}
    fields = _augment_file_metadata(fields, file_path, file_name)
    ocr_text = str(extracted.get("ocr_text") or "")
    fields = normalize_extracted_fields(material_type, fields, ocr_text=ocr_text, candidate_name=case.candidate_name)
    quality_notes = [str(item) for item in (extracted.get("quality_notes") or [])]
    if quality_prefix:
        quality_notes.insert(0, quality_prefix)
    base_review = review_material(
        material_type=material_type,
        candidate_name=case.candidate_name,
        expected_onboard_date=case.expected_onboard_date,
        ocr_text=ocr_text,
        mock_extracted_fields=fields,
    )
    if review_source:
        base_review.review_source = review_source
    ai_matched_labels: set[str] = set()
    if use_gemini_judgement:
        ai_matched_labels = await _ai_requirement_context_matches(case, material_type, base_review, ocr_text)
    reviewed = _apply_requirement_context_review(
        case,
        material_type,
        base_review,
        ocr_text,
        ai_matched_labels=ai_matched_labels,
    )
    if not use_gemini_judgement:
        return reviewed, ocr_text, quality_notes
    return reviewed, ocr_text, quality_notes


def _apply_requirement_context_review(
    case: CandidateCase,
    material_type: str,
    review: MaterialReviewResult,
    ocr_text: str,
    *,
    ai_matched_labels: set[str] | None = None,
) -> MaterialReviewResult:
    current_payload = _material_payload_text(review.extracted_fields, ocr_text)
    if not _looks_relevant_for_material(material_type, current_payload, review.extracted_fields):
        return review
    if review.decision != "auto_approved":
        return review
    if material_type in SUPPORTING_RESUME_REQUIRED_TYPES and not _has_resume_context(case):
        return _needs_resume_context_review(material_type, review)

    requirements = _material_context_requirements(case, material_type)
    if material_type == "degree_certificate" and not requirements:
        return _needs_resume_education_context_review(review)
    if not requirements:
        return review

    payloads = [(review.extracted_fields, ocr_text)]
    payloads.extend(_previous_material_payloads(case, material_type))
    matched_by_ai = ai_matched_labels or set()
    missing = [
        label
        for label, markers in requirements
        if label not in matched_by_ai and not _requirement_is_met(material_type, label, markers, payloads)
    ]
    if not missing:
        if matched_by_ai and "ark_compare" not in review.review_source:
            review.review_source = f"{review.review_source}+ark_compare"
        return review

    standard = get_material_standard(material_type)
    material_name = standard.display_name if standard else material_type
    reason = f"本次{material_name}已通过单份材料检查，但还缺：{'、'.join(missing[:6])}。请继续上传缺少的文件。"
    if not any(check.rule == "需继续补充材料" and check.reason == reason for check in review.checks):
        review.checks.append(MaterialCheck(rule="需继续补充材料", status="fail", reason=reason))
    review.decision = "auto_rejected"
    review.message_to_candidate = reason
    if review.review_source == "rules":
        review.review_source = "rules+requirement_context"
    return review


def _needs_resume_context_review(material_type: str, review: MaterialReviewResult) -> MaterialReviewResult:
    standard = get_material_standard(material_type)
    material_name = standard.display_name if standard else material_type
    if review.decision == "auto_rejected" and not _is_requirement_supplement_reason(review.message_to_candidate):
        reason = f"请先上传求职简历，再继续上传{material_name}。本次文件还存在其他问题，请同时按上方反馈修正。"
    else:
        reason = f"本次{material_name}已通过单份材料检查，但还缺：求职简历。请先上传求职简历，再继续上传对应证明材料。"
    if not any(check.rule == "需先上传简历" for check in review.checks):
        review.checks.append(MaterialCheck(rule="需先上传简历", status="fail", reason=reason))
    review.decision = "auto_rejected"
    review.message_to_candidate = reason
    if review.review_source == "rules":
        review.review_source = "rules+requirement_context"
    return review


def _needs_resume_education_context_review(review: MaterialReviewResult) -> MaterialReviewResult:
    reason = "已收到学历、学位证书，但没有从求职简历中识别到可核对的求学经历。请先上传包含完整教育经历的求职简历，再继续上传对应学历、学位证书。"
    if not any(check.rule == "需先识别简历求学经历" for check in review.checks):
        review.checks.append(MaterialCheck(rule="需先识别简历求学经历", status="fail", reason=reason))
    review.decision = "auto_rejected"
    review.message_to_candidate = reason
    if review.review_source == "rules":
        review.review_source = "rules+requirement_context"
    return review


async def _ai_requirement_context_matches(
    case: CandidateCase,
    material_type: str,
    review: MaterialReviewResult,
    ocr_text: str,
) -> set[str]:
    if material_type not in AI_CONTEXT_COMPARE_TYPES:
        return set()
    if review.decision != "auto_approved":
        return set()
    current_payload = _material_payload_text(review.extracted_fields, ocr_text)
    if not _looks_relevant_for_material(material_type, current_payload, review.extracted_fields):
        return set()
    if material_type in SUPPORTING_RESUME_REQUIRED_TYPES and not _has_resume_context(case):
        return set()

    requirements = _material_context_requirements(case, material_type)
    if not requirements:
        return set()
    payloads = [(review.extracted_fields, ocr_text)]
    payloads.extend(_previous_material_payloads(case, material_type))
    missing = [label for label, markers in requirements if not _requirement_is_met(material_type, label, markers, payloads)]
    if not missing or not is_ark_material_ocr_available():
        return set()

    try:
        result = await compare_material_requirements_with_ark(
            material_type=material_type,
            candidate_name=case.candidate_name,
            requirements=missing,
            resume_context=_resume_context_payload(case, material_type),
            material_evidence={
                "current": {"fields": review.extracted_fields, "ocr_text": _compact_text(ocr_text, 2500)},
                "previous_same_type": [
                    {"fields": fields, "ocr_text": _compact_text(text, 1200)}
                    for fields, text in _previous_material_payloads(case, material_type)
                ],
            },
        )
    except Exception as exc:
        logger.warning(
            "ark_requirement_compare_failed case_id=%s material_type=%s error_type=%s",
            case.id,
            material_type,
            type(exc).__name__,
        )
        return set()
    return _strict_ai_matched_labels(result, set(missing))


def _material_context_requirements(case: CandidateCase, material_type: str) -> list[tuple[str, tuple[str, ...]]]:
    if material_type == "degree_certificate":
        requirements: list[tuple[str, tuple[str, ...]]] = []
        for item in _resume_education_items(case):
            label = _education_item_label(item)
            markers = _education_item_markers(item)
            requirements.append((f"{label}毕业证书", markers))
            requirements.append((f"{label}学位证书", markers))
        return requirements
    if material_type == "employment_agreement":
        return [
            ("就业协议书", ("就业协议书", "就业协议", "三方协议")),
            ("就业推荐表", ("就业推荐表", "毕业生推荐表", "推荐表")),
        ]
    if material_type == "award_certificate":
        items = _resume_supporting_items(case, ("职称资格", "获奖信息"))
        return [(item, _resume_item_markers(item)) for item in items]
    if material_type == "professional_certificate":
        items = _resume_supporting_items(case, ("职称资格",))
        return [(item, _resume_item_markers(item)) for item in items]
    return []


def _has_resume_context(case: CandidateCase) -> bool:
    return _latest_resume_material(case) is not None


def is_material_required_for_case(case: CandidateCase, material_type: str) -> bool:
    if material_type == "award_certificate" and _has_resume_context(case):
        return bool(_resume_supporting_items(case, ("职称资格", "获奖信息")))
    return True


def _resume_supporting_items(case: CandidateCase, keys: tuple[str, ...]) -> list[str]:
    items: list[str] = []
    material = _latest_resume_material(case)
    if material:
        fields = _resume_fields(case, material)
        for key in keys:
            items.extend(_flatten_resume_items(fields.get(key)))
    deduped: list[str] = []
    for item in items:
        value = _compact_text(item, 80).strip(" ，,；;。")
        if len(value) >= 2 and value not in deduped:
            deduped.append(value)
    return deduped[:8]


def _resume_education_items(case: CandidateCase) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    material = _latest_resume_material(case)
    if material:
        fields = _resume_fields(case, material)
        for item in _as_list(fields.get("求学经历")):
            row = _education_row(item)
            if row and row not in rows:
                rows.append(row)
    return rows[:8]


def _education_row(item: Any) -> dict[str, str]:
    if isinstance(item, dict):
        row = {
            "学校": _first_text(item, ("学校", "院校", "毕业院校", "学历学校")),
            "学历层次": _first_text(item, ("学历层次", "学历", "教育层次")),
            "学位类别": _first_text(item, ("学位类别", "学位")),
            "专业": _first_text(item, ("专业",)),
        }
        return {key: value for key, value in row.items() if value}
    text = _compact_text(str(item or ""), 120)
    if not text:
        return {}
    row = {
        "学校": _extract_school(text),
        "学历层次": _extract_education_level(text),
        "学位类别": _extract_degree_level(text),
        "专业": _extract_major(text),
    }
    return {key: value for key, value in row.items() if value}


def _education_item_label(item: dict[str, str]) -> str:
    parts = [item.get("学校", ""), item.get("学历层次", "") or item.get("学位类别", "")]
    label = " ".join(part for part in parts if part).strip()
    return label or "简历中的求学经历"


def _education_item_markers(item: dict[str, str]) -> tuple[str, ...]:
    markers: list[str] = []
    for key in ("学校", "学历层次", "学位类别", "专业"):
        value = _normalize_supporting_text(item.get(key, ""))
        if value and value not in markers:
            markers.append(value)
    return tuple(markers)


def _resume_context_payload(case: CandidateCase, material_type: str) -> dict[str, Any]:
    latest_resume = _latest_resume_material(case)
    if not latest_resume:
        return {}
    fields = _resume_fields(case, latest_resume)
    keys = ("求学经历",) if material_type == "degree_certificate" else ("职称资格", "获奖信息")
    return {
        "fields": {key: fields.get(key) for key in keys if fields.get(key)},
        "ocr_text": _compact_text(latest_resume.ocr_text or "", 2500),
    }


def _flatten_resume_items(value: Any) -> list[str]:
    if value in (None, "", False, [], {}):
        return []
    if isinstance(value, list):
        items: list[str] = []
        for item in value:
            items.extend(_flatten_resume_items(item))
        return items
    if isinstance(value, dict):
        preferred = value.get("名称") or value.get("证书名称") or value.get("奖项名称") or value.get("职称") or value.get("资格")
        if preferred:
            return [str(preferred)]
        return [" ".join(str(item) for item in value.values() if item)]
    text = str(value)
    return [item for item in re.split(r"[\n；;]+", text) if item.strip()]


def _as_list(value: Any) -> list[Any]:
    if value in (None, "", False, [], {}):
        return []
    return value if isinstance(value, list) else [value]


def _first_text(item: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = item.get(key)
        if value in (None, "", False, [], {}):
            continue
        return _compact_text(str(value), 80)
    return ""


def _extract_school(text: str) -> str:
    match = re.search(r"[\u4e00-\u9fffA-Za-z0-9（）()·]{2,30}(?:大学|学院|学校|University|College|Institute)", text)
    return _compact_text(match.group(0), 80) if match else ""


def _extract_education_level(text: str) -> str:
    for level in ("博士研究生", "硕士研究生", "本科", "专科", "大专", "博士", "硕士", "学士"):
        if level in text:
            return level
    return ""


def _extract_degree_level(text: str) -> str:
    for level in ("博士学位", "硕士学位", "学士学位", "博士", "硕士", "学士"):
        if level in text:
            return level
    return ""


def _extract_major(text: str) -> str:
    match = re.search(r"[\u4e00-\u9fffA-Za-z0-9（）()·]{2,30}(?:专业|学|工程|管理|经济|金融|会计|法律)", text)
    return _compact_text(match.group(0).removesuffix("专业"), 80) if match else ""


def _resume_item_markers(item: str) -> tuple[str, ...]:
    compact = re.sub(r"\s+", "", item)
    markers = [compact]
    markers.extend(match.group(0) for match in re.finditer(r"[\u4e00-\u9fffA-Za-z0-9]{3,}", compact))
    return tuple(dict.fromkeys(marker for marker in markers if marker))


def _previous_material_payloads(case: CandidateCase, material_type: str) -> list[tuple[dict[str, Any], str]]:
    payloads: list[tuple[dict[str, Any], str]] = []
    resume_cutoff = _latest_resume_material(case) if material_type in SUPPORTING_RESUME_REQUIRED_TYPES else None
    if resume_cutoff and not _has_material_order_key(resume_cutoff):
        resume_cutoff = None
    for material in case.materials:
        if material.material_type != material_type:
            continue
        if resume_cutoff and _material_sort_key(material) <= _material_sort_key(resume_cutoff):
            continue
        if material.review_status in {"auto_rejected", "rejected"} and not _is_requirement_supplement_reason(material.review_reason or ""):
            continue
        payloads.append((_material_fields(material), material.ocr_text or material.file_name or ""))
    return payloads


def _latest_resume_material(case: CandidateCase) -> MaterialSubmission | None:
    return next(
        (
            material
            for material in sorted(case.materials, key=_material_sort_key, reverse=True)
            if material.material_type == "resume" and material.review_status not in {"auto_rejected", "rejected"}
        ),
        None,
    )


def _resume_fields(case: CandidateCase, material: MaterialSubmission) -> dict[str, Any]:
    return normalize_extracted_fields(
        "resume",
        _material_fields(material),
        ocr_text=material.ocr_text or "",
        candidate_name=case.candidate_name,
    )


def _material_sort_key(material: MaterialSubmission) -> tuple[bool, Any, int]:
    return (material.created_at is not None, material.created_at or 0, material.id or 0)


def _has_material_order_key(material: MaterialSubmission) -> bool:
    return material.created_at is not None or bool(material.id)


def _material_fields(material: MaterialSubmission) -> dict[str, Any]:
    try:
        value = json.loads(material.extracted_fields_json or "{}")
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def _requirement_is_met(
    material_type: str,
    label: str,
    markers: tuple[str, ...],
    payloads: list[tuple[dict[str, Any], str]],
) -> bool:
    if material_type == "degree_certificate":
        return any(_degree_payload_matches(label, markers, fields, text) for fields, text in payloads)
    if material_type in {"award_certificate", "professional_certificate"}:
        return any(_supporting_payload_matches(label, markers, fields, text) for fields, text in payloads)
    evidence = re.sub(r"\s+", "", " ".join(_material_payload_text(fields, text) for fields, text in payloads))
    if any(marker and marker in evidence for marker in markers):
        return True
    return False


def _supporting_payload_matches(label: str, markers: tuple[str, ...], fields: dict[str, Any], text: str) -> bool:
    evidence = re.sub(r"\s+", "", _material_payload_text(fields, text))
    if any(marker and marker in evidence for marker in markers):
        return True
    if _supporting_component_match(label, evidence):
        return True
    return _supporting_item_fuzzy_match(label, _material_payload_text(fields, text))


def _degree_payload_matches(label: str, markers: tuple[str, ...], fields: dict[str, Any], text: str) -> bool:
    evidence = _normalize_supporting_text(_material_payload_text(fields, text))
    if _truthy_field(fields.get("疑似学信网报告")) or "学信网" in evidence or "在线验证报告" in evidence:
        return False

    needs_graduation = "毕业" in label
    cert_field = "检测到毕业证书" if needs_graduation else "检测到学位证书"
    cert_keywords = ("毕业证书", "毕业证") if needs_graduation else ("学位证书", "学位证", "学士学位", "硕士学位", "博士学位")
    has_certificate_type = _truthy_field(fields.get(cert_field)) or any(keyword in evidence for keyword in cert_keywords)
    if not has_certificate_type:
        return False

    school_markers = [marker for marker in markers if any(suffix in marker for suffix in ("大学", "学院", "学校", "University", "College", "Institute"))]
    if school_markers and not any(marker in evidence for marker in school_markers):
        return False

    level_markers = [marker for marker in markers if _education_level_aliases(marker)]
    for marker in level_markers:
        aliases = _education_level_aliases(marker)
        if aliases and not any(alias in evidence for alias in aliases):
            return False
    return True


def _supporting_component_match(label: str, evidence: str) -> bool:
    normalized_label = _normalize_supporting_text(label)
    normalized_evidence = _normalize_supporting_text(evidence)
    orgs = re.findall(r"[\u4e00-\u9fffA-Za-z0-9·]{2,30}(?:大学|学院|学校|公司|集团|协会|委员会|中心|办公室)", normalized_label)
    if orgs and not any(org in normalized_evidence for org in orgs):
        return False
    terms = _supporting_distinctive_terms(normalized_label)
    if not terms:
        return False
    return any(term in normalized_evidence or _remove_award_rank(term) in _remove_award_rank(normalized_evidence) for term in terms)


def _supporting_distinctive_terms(text: str) -> list[str]:
    candidates = re.findall(
        r"[\u4e00-\u9fffA-Za-z0-9·]{2,40}(?:奖学金|优秀学生|优秀毕业生|荣誉称号|资格证书|职称证书|证书|竞赛|奖)",
        text,
    )
    if not candidates:
        candidates = [text]
    generic = {"奖", "证书", "荣誉", "奖学金", "优秀"}
    terms: list[str] = []
    for item in candidates:
        cleaned = re.sub(r"[\u4e00-\u9fffA-Za-z0-9·]{2,30}(?:大学|学院|学校|公司|集团|协会|委员会|中心|办公室)", "", item)
        cleaned = _normalize_supporting_text(cleaned)
        if len(cleaned) >= 3 and cleaned not in generic and cleaned not in terms:
            terms.append(cleaned)
    return terms


def _education_level_aliases(marker: str) -> tuple[str, ...]:
    value = _normalize_supporting_text(marker)
    if not value:
        return ()
    if "本科" in value or "学士" in value:
        return ("本科", "学士", "学士学位")
    if "硕士" in value:
        return ("硕士", "硕士研究生", "硕士学位")
    if "博士" in value:
        return ("博士", "博士研究生", "博士学位")
    if "专科" in value or "大专" in value:
        return ("专科", "大专")
    return ()


def _strict_ai_matched_labels(result: dict[str, Any], candidates: set[str]) -> set[str]:
    matches = result.get("matches") or result.get("匹配结果") or []
    if not isinstance(matches, list):
        return set()
    accepted: set[str] = set()
    for item in matches:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("requirement") or item.get("项目") or "")
        if label not in candidates:
            continue
        if not _truthy_field(item.get("matched")):
            continue
        if not _high_confidence(item.get("confidence") or item.get("置信度")):
            continue
        accepted.add(label)
    return accepted


def _high_confidence(value: Any) -> bool:
    if isinstance(value, (int, float)):
        return value >= 0.8
    text = str(value or "").strip().lower()
    return text in {"high", "高", "高置信", "high_confidence", "0.8", "0.9", "1"}


def _supporting_item_fuzzy_match(label: str, evidence: str) -> bool:
    normalized_label = _normalize_supporting_text(label)
    normalized_evidence = _normalize_supporting_text(evidence)
    if not normalized_label or not normalized_evidence:
        return False
    if normalized_label in normalized_evidence:
        return True
    rankless_label = _remove_award_rank(normalized_label)
    rankless_evidence = _remove_award_rank(normalized_evidence)
    if len(rankless_label) >= 4 and rankless_label in rankless_evidence:
        return True
    fragments = [
        _normalize_supporting_text(fragment)
        for fragment in re.split(r"[，,。；;\n\r\t /|、]+", evidence)
        if fragment.strip()
    ]
    for fragment in fragments:
        if len(fragment) >= 3 and SequenceMatcher(None, normalized_label, fragment).ratio() >= 0.72:
            return True
        rankless_fragment = _remove_award_rank(fragment)
        if len(rankless_label) >= 4 and (
            rankless_label in rankless_fragment or SequenceMatcher(None, rankless_label, rankless_fragment).ratio() >= 0.78
        ):
            return True
    return False


def _normalize_supporting_text(text: str) -> str:
    value = re.sub(r"\s+", "", str(text or ""))
    replacements = {
        "壹等": "一等",
        "贰等": "二等",
        "叁等": "三等",
        "丙等": "三等",
        "乙等": "二等",
        "甲等": "一等",
        "三等奖": "三等奖",
    }
    for source, target in replacements.items():
        value = value.replace(source, target)
    return value


def _remove_award_rank(text: str) -> str:
    value = _normalize_supporting_text(text)
    value = re.sub(r"(?:一等|二等|三等|甲等|乙等|丙等|一等奖|二等奖|三等奖)", "", value)
    return value


def _material_payload_text(fields: dict[str, Any], ocr_text: str) -> str:
    return f"{ocr_text} {_flatten_text(fields)}"


def _flatten_text(value: Any) -> str:
    if value in (None, "", False, [], {}):
        return ""
    if isinstance(value, dict):
        return " ".join(_flatten_text(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(_flatten_text(item) for item in value)
    return str(value)


def _truthy_field(value: Any) -> bool:
    return value in (True, "true", "True", "是", "有", "detected", "yes", "YES")


def _is_requirement_supplement_reason(reason: str) -> bool:
    return any(keyword in (reason or "") for keyword in ("还缺", "继续上传缺少", "请继续上传", "请先上传求职简历"))


def _looks_relevant_for_material(material_type: str, text: str, fields: dict[str, Any]) -> bool:
    actual_type = str(fields.get("实际材料类型") or "")
    if fields.get("材料类型匹配") is True:
        return True
    if material_type == "degree_certificate":
        if fields.get("疑似学信网报告") is True or "学信网" in text or "在线验证报告" in text:
            return False
        return any(keyword in text for keyword in ("毕业证书", "学位证书", "毕业证", "学位证"))
    if material_type == "employment_agreement":
        return any(keyword in text for keyword in ("就业协议", "三方协议", "就业推荐表", "毕业生推荐表"))
    if material_type in {"award_certificate", "professional_certificate"}:
        return actual_type in {"获奖证明", "职称/资格证书"} or any(
            keyword in text for keyword in ("获奖", "奖学金", "荣誉证书", "优秀学生", "资格证书", "职称证书")
        )
    return False


def _file_extension_review(material_type: str, file_name: str) -> MaterialReviewResult | None:
    standard = get_material_standard(material_type)
    if not standard:
        return None
    extension = Path(file_name).suffix.lower().lstrip(".")
    if not extension or extension in standard.accepted_formats:
        return None
    accepted = " / ".join(standard.accepted_formats)
    return MaterialReviewResult(
        material_type=material_type,
        decision="auto_rejected",
        extracted_fields={"file_format": extension},
        checks=[
            MaterialCheck(
                rule="文件格式符合要求",
                status="fail",
                reason=f"当前文件格式为 {extension}，要求格式：{accepted}",
            )
        ],
        message_to_candidate=f"请重新上传 {accepted} 格式的材料文件。",
        review_source="precheck",
    )


def _augment_file_metadata(fields: dict[str, Any], file_path: str, file_name: str) -> dict[str, Any]:
    enriched = dict(fields)
    extension = Path(file_name).suffix.lower().lstrip(".")
    if extension:
        enriched["file_format"] = extension
    try:
        enriched["file_size_kb"] = round(Path(file_path).stat().st_size / 1024, 1)
    except OSError:
        pass
    return enriched


def _fallback_review_after_ocr_failure(
    case: CandidateCase,
    material_type: str,
    file_path: str,
    file_name: str,
) -> tuple[MaterialReviewResult, str, list[str]] | None:
    if material_type == "resume":
        return _fallback_resume_review_after_ocr_failure(case, file_path, file_name)
    if material_type != "degree_certificate":
        return None
    text = _extract_pdf_text(file_path, file_name)
    if not _looks_like_xuexin_report(text):
        return None
    return _degree_certificate_xuexin_review(
        case,
        file_path,
        file_name,
        text,
        review_source="rules+pdf_text_fallback",
        quality_note="Gemini OCR 失败，已使用 PDF 文本兜底识别为学信网在线验证报告。",
    )


def _degree_certificate_xuexin_review(
    case: CandidateCase,
    file_path: str,
    file_name: str,
    text: str,
    *,
    review_source: str,
    quality_note: str,
) -> tuple[MaterialReviewResult, str, list[str]]:
    material_type = "degree_certificate"
    fields = _augment_file_metadata(
        {
            "实际材料类型": "学信网在线验证报告",
            "检测到的材料类型列表": ["学信网在线验证报告"],
            "材料类型匹配": False,
            "证书类型": "学信网在线验证报告",
            "检测到毕业证书": False,
            "检测到学位证书": False,
            "疑似学信网报告": True,
        },
        file_path,
        file_name,
    )
    review = review_material(
        material_type=material_type,
        candidate_name=case.candidate_name,
        expected_onboard_date=case.expected_onboard_date,
        ocr_text=text,
        mock_extracted_fields=fields,
    )
    standard = get_material_standard(material_type)
    if standard:
        review.message_to_candidate = standard.candidate_feedback
    review.review_source = review_source
    return review, text, [quality_note]


def _local_text_precheck_review(
    case: CandidateCase,
    material_type: str,
    file_path: str,
    file_name: str,
) -> tuple[MaterialReviewResult, str, list[str]] | None:
    if material_type == "resume":
        result = _fallback_resume_review_after_ocr_failure(case, file_path, file_name)
        if not result:
            return None
        review, ocr_text, quality_notes = result
        review.review_source = "rules+local_text_precheck"
        return review, ocr_text, ["已优先使用本地文本识别，未调用视觉模型。", *quality_notes]

    text = _extract_local_text(file_path, file_name)
    if not _compact_text(text, 30):
        return None

    if material_type != "degree_certificate":
        return _local_text_mismatch_review(case, material_type, file_path, file_name, text)

    if _looks_like_xuexin_report(text):
        return _degree_certificate_xuexin_review(
            case,
            file_path,
            file_name,
            text,
            review_source="rules+local_text_precheck",
            quality_note="已优先使用本地文本识别，未调用视觉模型。",
        )
    return _local_text_mismatch_review(case, material_type, file_path, file_name, text)


def _local_text_mismatch_review(
    case: CandidateCase,
    material_type: str,
    file_path: str,
    file_name: str,
    text: str,
) -> tuple[MaterialReviewResult, str, list[str]] | None:
    actual_type = _infer_non_resume_material_type(text, file_name)
    if actual_type == "非求职简历文档":
        return None
    if material_type in _compatible_material_codes(actual_type):
        return None

    fields = _augment_file_metadata(
        {
            "实际材料类型": actual_type,
            "检测到的材料类型列表": [actual_type],
            "材料类型匹配": False,
        },
        file_path,
        file_name,
    )
    review = review_material(
        material_type=material_type,
        candidate_name=case.candidate_name,
        expected_onboard_date=case.expected_onboard_date,
        ocr_text=text,
        mock_extracted_fields=fields,
    )
    standard = get_material_standard(material_type)
    if standard:
        review.message_to_candidate = f"请重新上传{standard.display_name}，当前文件看起来是{actual_type}。"
    review.decision = "auto_rejected"
    review.review_source = "rules+local_text_precheck"
    return review, text, ["已通过本地文本预判为材料类型不匹配，未调用视觉模型。"]


def _compatible_material_codes(actual_type: str) -> set[str]:
    mapping = {
        "学信网在线验证报告": {"xuexin_report"},
        "身份证件": {"id_card"},
        "银行卡": {"bank_card"},
        "学历学位证书": {"degree_certificate"},
        "离职证明": {"resignation_certificate"},
        "体检报告": {"medical_report"},
        "就业协议或推荐表": {"employment_agreement"},
        "职称/资格证书": {"professional_certificate", "award_certificate"},
        "获奖证明": {"award_certificate"},
    }
    return mapping.get(actual_type, set())


def _fallback_resume_review_after_ocr_failure(
    case: CandidateCase,
    file_path: str,
    file_name: str,
) -> tuple[MaterialReviewResult, str, list[str]] | None:
    text = _extract_local_text(file_path, file_name)
    if not _compact_text(text, 30):
        return None
    if not _looks_like_resume_text(text, file_name, case.candidate_name):
        actual_type = _infer_non_resume_material_type(text, file_name)
        fields = _augment_file_metadata(
            {
                "实际材料类型": actual_type,
                "检测到的材料类型列表": [actual_type],
                "材料类型匹配": False,
                "简历摘要": "",
            },
            file_path,
            file_name,
        )
        review = review_material(
            material_type="resume",
            candidate_name=case.candidate_name,
            expected_onboard_date=case.expected_onboard_date,
            ocr_text=text,
            mock_extracted_fields=fields,
        )
        review.review_source = "rules+local_text_fallback"
        standard = get_material_standard("resume")
        if standard:
            review.message_to_candidate = standard.candidate_feedback
        return review, text, ["OCR 失败，已使用本地文本兜底识别为非求职简历材料。"]

    fields = _augment_file_metadata(
        {
            "实际材料类型": "求职简历",
            "检测到的材料类型列表": ["求职简历"],
            "材料类型匹配": True,
            "姓名": case.candidate_name if case.candidate_name and case.candidate_name in text else "",
            "简历摘要": _compact_text(text, 500),
        },
        file_path,
        file_name,
    )
    fields = normalize_extracted_fields("resume", fields, ocr_text=text, candidate_name=case.candidate_name)
    review = review_material(
        material_type="resume",
        candidate_name=case.candidate_name,
        expected_onboard_date=case.expected_onboard_date,
        ocr_text=text,
        mock_extracted_fields=fields,
    )
    review.review_source = "rules+pdf_text_fallback"
    return review, text, ["OCR 失败，已使用 PDF 文本兜底识别求职简历。"]


def _extract_local_text(file_path: str, file_name: str) -> str:
    suffix = Path(file_name).suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf_text(file_path, file_name)
    if suffix == ".docx":
        return _extract_docx_text(file_path)
    return ""


def _extract_pdf_text(file_path: str, file_name: str) -> str:
    if Path(file_name).suffix.lower() != ".pdf":
        return ""
    try:
        from pypdf import PdfReader

        reader = PdfReader(file_path)
        parts: list[str] = []
        total = 0
        for page in reader.pages:
            text = page.extract_text() or ""
            if not text:
                continue
            parts.append(text)
            total += len(text)
            if total >= LOCAL_TEXT_MAX_CHARS:
                break
        return "\n".join(parts)[:LOCAL_TEXT_MAX_CHARS]
    except Exception as exc:
        logger.warning("pdf_text_fallback_failed error_type=%s", type(exc).__name__)
        return ""


def _extract_docx_text(file_path: str) -> str:
    try:
        with zipfile.ZipFile(file_path) as archive:
            xml_files = [name for name in archive.namelist() if name.startswith("word/") and name.endswith(".xml")]
            parts: list[str] = []
            total = 0
            for name in xml_files:
                root = ElementTree.fromstring(archive.read(name))
                for node in root.iter():
                    if node.tag.endswith("}t") and node.text:
                        parts.append(node.text)
                        total += len(node.text)
                        if total >= LOCAL_TEXT_MAX_CHARS:
                            return "\n".join(parts)[:LOCAL_TEXT_MAX_CHARS]
            return "\n".join(parts)[:LOCAL_TEXT_MAX_CHARS]
    except Exception as exc:
        logger.warning("docx_text_fallback_failed error_type=%s", type(exc).__name__)
        return ""


def _looks_like_xuexin_report(text: str) -> bool:
    return any(keyword in text for keyword in ("学信网", "在线验证报告", "中国高等教育学历", "中国高等教育学位"))


def _looks_like_resume_text(text: str, file_name: str, candidate_name: str) -> bool:
    compact = _compact_text(text, 1200)
    if len(compact) < 10:
        return False
    resume_markers = ("简历", "求职", "教育经历", "求学经历", "工作经历", "实习经历", "项目经历", "获奖", "资格证书")
    if any(marker in file_name for marker in ("简历", "resume", "Resume", "CV")):
        return True
    if len(compact) < 30:
        return False
    if candidate_name and candidate_name in compact and any(marker in compact for marker in resume_markers):
        return True
    return sum(1 for marker in resume_markers if marker in compact) >= 2


def _infer_non_resume_material_type(text: str, file_name: str) -> str:
    compact = _compact_text(f"{file_name} {text}", 1200)
    if any(keyword in compact for keyword in ("学信网", "在线验证报告", "中国高等教育学历", "中国高等教育学位")):
        return "学信网在线验证报告"
    if any(keyword in compact for keyword in ("居民身份证", "公民身份号码", "签发机关", "有效期限")):
        return "身份证件"
    if any(keyword in compact for keyword in ("银行卡", "借记卡", "开户行", "银行账号", "卡号")):
        return "银行卡"
    if any(keyword in compact for keyword in ("就业协议书", "就业协议", "三方协议", "就业推荐表", "毕业生推荐表")):
        return "就业协议或推荐表"
    if any(keyword in compact for keyword in ("职称证书", "职业资格证书", "专业技术资格", "资格证书")):
        return "职称/资格证书"
    if any(keyword in compact for keyword in ("获奖", "奖学金", "荣誉证书", "奖励证书", "优秀学生", "优秀毕业生", "一等奖", "二等奖", "三等奖")):
        return "获奖证明"
    if any(
        keyword in compact
        for keyword in (
            "毕业证书",
            "学位证书",
            "学历证书",
            "普通高等学校毕业证书",
            "普通高等教育毕业证书",
            "学士学位",
            "硕士学位",
            "博士学位",
        )
    ):
        return "学历学位证书"
    if any(keyword in compact for keyword in ("离职证明", "解除劳动合同", "终止劳动合同")):
        return "离职证明"
    if any(keyword in compact for keyword in ("体检报告", "总检结论", "体检中心")):
        return "体检报告"
    return "非求职简历文档"


def _compact_text(text: str, limit: int) -> str:
    return " ".join((text or "").split())[:limit]


def _manual_review(material_type: str, message: str, reason: str) -> MaterialReviewResult:
    return MaterialReviewResult(
        material_type=material_type,
        decision="manual_review",
        extracted_fields={},
        checks=[MaterialCheck(rule="自动审核", status="manual_review", reason=reason)],
        message_to_candidate=message,
        review_source="manual_fallback",
    )


def _review_reason(review: MaterialReviewResult) -> str:
    failed = [_friendly_check_reason(check) for check in review.checks if check.status != "pass"]
    failed = [item for index, item in enumerate(failed) if item and item not in failed[:index]]
    if not failed:
        return review.message_to_candidate[:1000]
    if review.decision == "auto_rejected":
        prefix = review.message_to_candidate or "材料未通过自动审核，请根据提示补充或重新提交。"
        failed = [item for item in failed if item != prefix and item not in prefix]
        if not failed:
            return prefix[:1000]
        return f"{prefix} 主要问题：{'；'.join(failed[:5])}"[:1000]
    if review.decision == "manual_review":
        prefix = review.message_to_candidate or "材料已收到，将转由 HR 人工审核。"
        return f"{prefix} 需要人工确认：{'；'.join(failed[:5])}"[:1000]
    return "；".join(failed[:5])[:1000]


def _friendly_check_reason(check: MaterialCheck) -> str:
    rule = check.rule
    reason = check.reason
    if rule == "材料类型匹配":
        actual = reason.removeprefix("实际识别为").strip() or "其他材料"
        return f"系统识别到上传内容像“{actual}”，与本材料项不匹配"
    if rule == "材料为学校发放证书":
        return "系统识别到学信网/在线验证报告，不能替代学校发放的毕业证书和学位证书"
    if rule == "毕业证书已提交":
        return "未检测到学校发放的毕业证书"
    if rule == "学位证书已提交":
        return "未检测到学校发放的学位证书"
    if rule.startswith("必填字段："):
        field_name = rule.split("：", 1)[1]
        return f"未能识别到{field_name}"
    if rule == "姓名一致":
        return f"材料姓名与候选人姓名不一致（{reason}）"
    if rule == "自动审核":
        return reason
    return reason


def _extract_record_id(response: dict[str, Any]) -> str | None:
    record = ((response.get("data") or {}).get("record") or {})
    return record.get("record_id") or record.get("id")
