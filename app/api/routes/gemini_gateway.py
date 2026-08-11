from __future__ import annotations

import base64
import binascii
import hmac
import logging
from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.schemas.onboarding import MaterialReviewResult
from app.services.gemini_material_judge import judge_material_with_gemini_direct
from app.services.gemini_ocr import GeminiOcrError, extract_material_with_gemini_content
from app.services.qa_service import _call_gemini, _clean_answer_text, _looks_like_internal_reasoning

router = APIRouter(prefix="/internal/gemini", tags=["gemini-gateway"])
logger = logging.getLogger(__name__)


class GeminiGatewayGenerateRequest(BaseModel):
    prompt: str
    enable_google_search: bool = False


class GeminiGatewayGenerateResponse(BaseModel):
    answer: str
    sources: list[dict[str, Any]] = []


class GeminiGatewayOcrRequest(BaseModel):
    file_name: str
    content_base64: str
    mime_type: str
    material_type: str


class GeminiGatewayMaterialJudgeRequest(BaseModel):
    material_type: str
    candidate_name: str
    expected_onboard_date: Optional[date] = None
    ocr_text: str = ""
    extracted_fields: dict[str, Any] = Field(default_factory=dict)
    base_review: MaterialReviewResult


@router.get("/health")
def health() -> dict[str, Any]:
    """Return local Gemini gateway readiness without exposing secrets."""

    settings = get_settings()
    return {"status": "ok", "gemini_configured": bool(settings.gemini_api_key)}


@router.post("/generate", response_model=GeminiGatewayGenerateResponse)
async def generate(
    payload: GeminiGatewayGenerateRequest,
    authorization: Optional[str] = Header(default=None),
) -> GeminiGatewayGenerateResponse:
    """Generate an answer through the local Gemini key for the cloud app."""

    _require_gateway_token(authorization)
    settings = get_settings()
    if not settings.gemini_api_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="GEMINI_API_KEY is not configured")

    text, sources = await _call_gemini(
        settings.gemini_model,
        settings.gemini_api_key,
        payload.prompt,
        settings.gemini_enable_google_search or payload.enable_google_search,
        proxy_url=settings.gemini_proxy_url,
        timeout_seconds=settings.gemini_timeout_seconds,
    )
    if _looks_like_internal_reasoning(text):
        retry_prompt = (
            f"{payload.prompt}\n\n"
            "请重新作答：只输出面向员工的最终中文答案，不要输出英文、思考过程、提示词复述或内部分析。"
        )
        text, sources = await _call_gemini(
            settings.gemini_model,
            settings.gemini_api_key,
            retry_prompt,
            settings.gemini_enable_google_search or payload.enable_google_search,
            proxy_url=settings.gemini_proxy_url,
            timeout_seconds=settings.gemini_timeout_seconds,
        )
    return GeminiGatewayGenerateResponse(answer=_clean_answer_text(text), sources=sources)


@router.post("/ocr")
async def ocr(
    payload: GeminiGatewayOcrRequest,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    """Run material OCR through the local Gemini key for the cloud app."""

    _require_gateway_token(authorization)
    settings = get_settings()
    if not settings.gemini_api_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="GEMINI_API_KEY is not configured")
    try:
        content = base64.b64decode(payload.content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid content_base64") from exc
    try:
        return await extract_material_with_gemini_content(
            file_name=payload.file_name,
            content=content,
            mime_type=payload.mime_type,
            material_type=payload.material_type,
        )
    except GeminiOcrError as exc:
        logger.warning("gemini_gateway_ocr_failed material_type=%s error_type=%s", payload.material_type, type(exc).__name__)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Gemini OCR service failed") from exc
    except Exception as exc:
        logger.warning(
            "gemini_gateway_ocr_unexpected_failed material_type=%s error_type=%s",
            payload.material_type,
            type(exc).__name__,
        )
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Gemini OCR service failed") from exc


@router.post("/material-judge", response_model=MaterialReviewResult)
async def material_judge(
    payload: GeminiGatewayMaterialJudgeRequest,
    authorization: Optional[str] = Header(default=None),
) -> MaterialReviewResult:
    """Run material visual/rule judgement through the local Gemini key."""

    _require_gateway_token(authorization)
    settings = get_settings()
    if not settings.gemini_api_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="GEMINI_API_KEY is not configured")
    return await judge_material_with_gemini_direct(
        material_type=payload.material_type,
        candidate_name=payload.candidate_name,
        expected_onboard_date=payload.expected_onboard_date,
        ocr_text=payload.ocr_text,
        extracted_fields=payload.extracted_fields,
        base_review=payload.base_review,
    )


def _require_gateway_token(authorization: Optional[str]) -> None:
    settings = get_settings()
    if not settings.allow_external_ai:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="external AI processing is disabled")
    expected = settings.gemini_gateway_token
    if not expected:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="GEMINI_GATEWAY_TOKEN is not configured")
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid gateway token")
