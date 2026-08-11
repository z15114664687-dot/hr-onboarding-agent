from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.untrusted_content import UNTRUSTED_DOCUMENT_NOTICE, wrap_untrusted_document
from app.schemas.onboarding import MaterialCheck, MaterialReviewResult
from app.services.material_standards import material_standard_prompt, material_visual_reference_prompt

DECISION_SEVERITY = {"auto_approved": 0, "manual_review": 1, "auto_rejected": 2}


async def judge_material_with_gemini(
    *,
    material_type: str,
    candidate_name: str,
    ocr_text: str,
    extracted_fields: dict[str, Any],
    base_review: MaterialReviewResult,
    expected_onboard_date: date | None = None,
) -> MaterialReviewResult:
    """Ask Gemini to review material quality and candidate feedback, with hard-rule guardrails."""

    settings = get_settings()
    if not settings.allow_external_ai:
        return base_review
    if settings.gemini_gateway_url and settings.gemini_gateway_token:
        return await _judge_material_with_gateway(
            material_type=material_type,
            candidate_name=candidate_name,
            ocr_text=ocr_text,
            extracted_fields=extracted_fields,
            base_review=base_review,
            expected_onboard_date=expected_onboard_date,
        )
    if settings.gemini_api_key:
        return await judge_material_with_gemini_direct(
            material_type=material_type,
            candidate_name=candidate_name,
            ocr_text=ocr_text,
            extracted_fields=extracted_fields,
            base_review=base_review,
            expected_onboard_date=expected_onboard_date,
        )
    return base_review


async def judge_material_with_gemini_direct(
    *,
    material_type: str,
    candidate_name: str,
    ocr_text: str,
    extracted_fields: dict[str, Any],
    base_review: MaterialReviewResult,
    expected_onboard_date: date | None = None,
) -> MaterialReviewResult:
    """Ask Gemini directly with the local API key."""

    settings = get_settings()
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")

    payload = {
        "systemInstruction": {"parts": [{"text": _system_prompt()}]},
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": _build_prompt(
                            material_type=material_type,
                            candidate_name=candidate_name,
                            expected_onboard_date=expected_onboard_date,
                            ocr_text=ocr_text,
                            extracted_fields=extracted_fields,
                            base_review=base_review,
                        )
                    }
                ],
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            "topP": 0.7,
            "maxOutputTokens": 1800,
            "responseMimeType": "application/json",
        },
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_model}:generateContent"
    async with httpx.AsyncClient(timeout=45) as client:
        response = await client.post(url, headers={"x-goog-api-key": settings.gemini_api_key}, json=payload)
        response.raise_for_status()

    data = response.json()
    parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
    raw_text = "".join(str(part.get("text", "")) for part in parts).strip()
    if not raw_text:
        return base_review
    return _merge_gemini_review(base_review, _parse_json_response(raw_text))


async def _judge_material_with_gateway(
    *,
    material_type: str,
    candidate_name: str,
    ocr_text: str,
    extracted_fields: dict[str, Any],
    base_review: MaterialReviewResult,
    expected_onboard_date: date | None = None,
) -> MaterialReviewResult:
    settings = get_settings()
    if not settings.gemini_gateway_url or not settings.gemini_gateway_token:
        return base_review

    timeout_seconds = settings.gemini_gateway_timeout_seconds
    timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 10.0))
    url = f"{settings.gemini_gateway_url.rstrip('/')}/internal/gemini/material-judge"
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {settings.gemini_gateway_token}"},
            json={
                "material_type": material_type,
                "candidate_name": candidate_name,
                "expected_onboard_date": expected_onboard_date.isoformat() if expected_onboard_date else None,
                "ocr_text": ocr_text,
                "extracted_fields": extracted_fields,
                "base_review": base_review.model_dump(mode="json"),
            },
        )
        response.raise_for_status()
    data = response.json()
    return MaterialReviewResult.model_validate(data)


def _system_prompt() -> str:
    return (
        "你是 HR 入职材料审核专家，负责根据附件1上传文件示例、入职材料清单和OCR结果做材料初审。"
        f"{UNTRUSTED_DOCUMENT_NOTICE}"
        "你需要判断材料是否符合上传标准、字段是否完整、是否需要候选人重新提交。"
        "必须遵守："
        "1. 不要放过硬性错误，例如缺少身份证背面、离职证明无盖章、体检报告缺总检结论、姓名不一致、文件模糊或页数不完整。"
        "中文名、英文名、拼音名可视为同一人，例如“林晓安”和“LIN Xiaoan”可判为一致。"
        "2. 如果本地规则已经判定 fail，你可以补充原因和反馈，但不能改成 auto_approved。"
        "3. 如果信息无法确认，使用 manual_review，不要编造。"
        "4. 候选人反馈要具体、可执行，说明需要重新上传什么、为什么不合格、按什么标准上传。"
        "5. OCR 字段不足时，结合 extracted_fields 中的视觉判断字段，例如实际材料类型、检测到的材料类型列表、"
        "检测到毕业证书、检测到学位证书、是否工商银行卡、单人照片、五官清晰等。"
        "6. 学历/学位证书必须是学校发放的毕业证书和学位证书，不能用学信网报告替代；只传其中一种要驳回并提示补充另一种。"
        "7. 求职简历需提取教育经历、工作经历、职称/资格、获奖信息，便于写入多维表格并核对后续证明材料。"
        "8. 工资卡必须能识别为中国工商银行/ICBC；银行卡没有姓名不要作为不合格原因。"
        "9. 工作证照片要按附件1示例判断照片本身，不要只看 OCR。"
        "10. 英文或中英文证明要用中文字段整理，并在 HR notes 中说明字段值是否为系统翻译。"
        "11. 如果 extracted_fields 显示 all_pages_upright=false 或 image_orientation_consistent=false，"
        "必须判为 auto_rejected，并明确提示候选人重新上传正向一致的文件。"
        "12. 只输出 JSON。"
    )


def _build_prompt(
    *,
    material_type: str,
    candidate_name: str,
    expected_onboard_date: date | None,
    ocr_text: str,
    extracted_fields: dict[str, Any],
    base_review: MaterialReviewResult,
) -> str:
    untrusted_evidence = wrap_untrusted_document(
        json.dumps(
            {
                "candidate_name": candidate_name,
                "ocr_text": ocr_text[:5000],
                "extracted_fields": extracted_fields,
            },
            ensure_ascii=False,
        )
    )
    return json.dumps(
        {
            "material_type": material_type,
            "expected_onboard_date": expected_onboard_date.isoformat() if expected_onboard_date else None,
            "upload_standard": material_standard_prompt(material_type),
            "visual_reference": material_visual_reference_prompt(material_type),
            "untrusted_document_evidence": untrusted_evidence,
            "base_review": base_review.model_dump(),
            "output_schema": {
                "decision": "auto_approved | auto_rejected | manual_review",
                "checks": [{"rule": "规则名", "status": "pass|fail|manual_review", "reason": "原因"}],
                "message_to_candidate": "给候选人的具体反馈",
                "hr_notes": "给HR看的复核说明",
            },
        },
        ensure_ascii=False,
    )


def _merge_gemini_review(base_review: MaterialReviewResult, gemini_data: dict[str, Any]) -> MaterialReviewResult:
    gemini_decision = str(gemini_data.get("decision") or base_review.decision)
    if gemini_decision not in DECISION_SEVERITY:
        gemini_decision = base_review.decision

    decision = _guarded_decision(base_review.decision, gemini_decision)
    gemini_checks = _parse_checks(gemini_data.get("checks"))
    checks = gemini_checks or base_review.checks
    message = str(gemini_data.get("message_to_candidate") or base_review.message_to_candidate)
    base_non_pass = [check for check in base_review.checks if check.status != "pass"]
    if base_non_pass and (decision != gemini_decision or not any(check.status != "pass" for check in checks)):
        checks = _merge_checks(base_review.checks, gemini_checks)
        message = base_review.message_to_candidate
    hr_notes = gemini_data.get("hr_notes")
    return MaterialReviewResult(
        material_type=base_review.material_type,
        decision=decision,
        extracted_fields=base_review.extracted_fields,
        checks=checks,
        message_to_candidate=message,
        hr_notes=str(hr_notes) if hr_notes else None,
        review_source="rules+gemini",
    )


def _merge_checks(base_checks: list[MaterialCheck], gemini_checks: list[MaterialCheck]) -> list[MaterialCheck]:
    merged = list(base_checks)
    existing = {(check.rule, check.status, check.reason) for check in merged}
    for check in gemini_checks:
        key = (check.rule, check.status, check.reason)
        if key not in existing:
            merged.append(check)
            existing.add(key)
    return merged


def _guarded_decision(base_decision: str, gemini_decision: str) -> str:
    if base_decision == "auto_rejected" and gemini_decision == "auto_approved":
        return "auto_rejected"
    if base_decision == "manual_review" and gemini_decision == "auto_approved":
        return "manual_review"
    return gemini_decision if DECISION_SEVERITY[gemini_decision] >= DECISION_SEVERITY[base_decision] else base_decision


def _parse_checks(value: Any) -> list[MaterialCheck]:
    if not isinstance(value, list):
        return []
    checks: list[MaterialCheck] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        status = item.get("status")
        if status not in {"pass", "fail", "manual_review"}:
            status = "manual_review"
        checks.append(
            MaterialCheck(
                rule=str(item.get("rule") or "Gemini复核"),
                status=status,
                reason=str(item.get("reason") or "未提供原因"),
            )
        )
    return checks


def _parse_json_response(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    return json.loads(cleaned)
