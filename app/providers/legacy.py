from __future__ import annotations

from app.providers.base import OCRRequest, OCRResult
from app.services.ark_material_ocr import extract_material_with_ark_content
from app.services.gemini_ocr import extract_material_with_gemini_content


class ArkOCRProvider:
    async def extract(self, request: OCRRequest) -> OCRResult:
        result = await extract_material_with_ark_content(
            file_name=request.file_name,
            content=request.content,
            mime_type=request.mime_type,
            material_type=request.material_type,
        )
        return _result("ark", result)


class GeminiOCRProvider:
    async def extract(self, request: OCRRequest) -> OCRResult:
        result = await extract_material_with_gemini_content(
            file_name=request.file_name,
            content=request.content,
            mime_type=request.mime_type,
            material_type=request.material_type,
        )
        return _result("gemini", result)


def _result(provider: str, value: dict) -> OCRResult:
    return OCRResult(
        ocr_text=str(value.get("ocr_text") or ""),
        extracted_fields=dict(value.get("extracted_fields") or {}),
        quality_notes=tuple(str(item) for item in value.get("quality_notes") or ()),
        provider=provider,
    )
