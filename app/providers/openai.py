from __future__ import annotations

import base64
import json
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.untrusted_content import UNTRUSTED_DOCUMENT_NOTICE
from app.providers.base import LLMRequest, LLMResult, OCRRequest, OCRResult


_STRUCTURED_OUTPUT_NAME = "hr_onboarding_result"
_OCR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ocr_text": {"type": "string"},
        "extracted_fields": {"type": "object", "additionalProperties": True},
        "quality_notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["ocr_text", "extracted_fields", "quality_notes"],
    "additionalProperties": False,
}


class OpenAIProvider:
    """Minimal Responses API adapter using the existing httpx dependency."""

    async def generate(self, request: LLMRequest) -> LLMResult:
        settings = _settings()
        payload: dict[str, Any] = {
            "model": settings.openai_model,
            "input": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
        }
        if request.json_schema:
            payload["text"] = {"format": _json_schema_format(request.json_schema)}
        data = await _post_responses(payload)
        return LLMResult(text=_output_text(data), provider="openai", model=settings.openai_model)

    async def extract(self, request: OCRRequest) -> OCRResult:
        settings = _settings()
        encoded = base64.b64encode(request.content).decode("ascii")
        prompt = (
            f"{UNTRUSTED_DOCUMENT_NOTICE}\n"
            f"Extract visible text and objective fields from this {request.material_type} document. "
            "Return JSON with ocr_text, extracted_fields, and quality_notes. Do not make approval decisions."
        )
        file_content: dict[str, str]
        data_url = f"data:{request.mime_type};base64,{encoded}"
        if request.mime_type == "application/pdf":
            file_content = {
                "type": "input_file",
                "filename": request.file_name,
                "file_data": data_url,
            }
        else:
            file_content = {"type": "input_image", "image_url": data_url}
        payload = {
            "model": settings.openai_model,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        file_content,
                    ],
                }
            ],
            "text": {"format": _json_schema_format(_OCR_SCHEMA, name="hr_document_extraction", strict=False)},
        }
        data = _json_object(_output_text(await _post_responses(payload)))
        return OCRResult(
            ocr_text=str(data.get("ocr_text") or ""),
            extracted_fields=dict(data.get("extracted_fields") or {}),
            quality_notes=tuple(str(item) for item in data.get("quality_notes") or ()),
            provider="openai",
        )


def _settings():
    settings = get_settings()
    if not settings.allow_external_ai:
        raise RuntimeError("external AI processing is disabled")
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    return settings


async def _post_responses(payload: dict[str, Any]) -> dict[str, Any]:
    settings = _settings()
    async with httpx.AsyncClient(timeout=settings.openai_timeout_seconds) as client:
        response = await client.post(
            f"{settings.openai_base_url.rstrip('/')}/responses",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json=payload,
        )
    response.raise_for_status()
    return response.json()


def _json_schema_format(
    schema: dict[str, Any], *, name: str = _STRUCTURED_OUTPUT_NAME, strict: bool = True
) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "name": name,
        "strict": strict,
        "schema": schema,
    }


def _json_object(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("provider returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError("provider returned a non-object JSON value")
    return value


def _output_text(data: dict[str, Any]) -> str:
    if data.get("output_text"):
        return str(data["output_text"])
    parts: list[str] = []
    for item in data.get("output") or ():
        for content in item.get("content") or ():
            if content.get("type") in {"output_text", "text"}:
                parts.append(str(content.get("text") or ""))
    text = "".join(parts).strip()
    if not text:
        raise RuntimeError("provider returned an empty response")
    return text
