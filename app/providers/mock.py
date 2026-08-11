from __future__ import annotations

import json

from app.core.untrusted_content import find_untrusted_content_risks
from app.providers.base import LLMRequest, LLMResult, OCRRequest, OCRResult


class MockLLMProvider:
    """Deterministic provider for local demos and tests; it never calls a network."""

    async def generate(self, request: LLMRequest) -> LLMResult:
        findings = find_untrusted_content_risks(request.user_prompt)
        payload = {
            "decision": "manual_review" if findings else "auto_approved",
            "summary": "Potential prompt injection detected." if findings else "Synthetic document reviewed.",
            "finding_codes": [finding.code for finding in findings],
        }
        return LLMResult(text=json.dumps(payload, ensure_ascii=False), provider="mock", model="deterministic-v1")


class MockOCRProvider:
    """Return stable synthetic extraction data based on the selected material type."""

    async def extract(self, request: OCRRequest) -> OCRResult:
        fields_by_type = {
            "resume": {"姓名": "林晓安", "求学经历": [{"学校": "星河大学", "学历层次": "本科"}]},
            "degree_certificate": {
                "姓名": "林晓安",
                "学校": "星河大学",
                "学历层次": "本科",
                "检测到毕业证书": True,
                "检测到学位证书": True,
                "材料类型匹配": True,
            },
            "medical_report": {
                "姓名": "林晓安",
                "体检日期": "2026-08-01",
                "体检机构": "示例健康中心",
                "总检结论": "未见异常",
                "有结论页": True,
                "材料类型匹配": True,
            },
        }
        fields = fields_by_type.get(request.material_type, {"材料类型匹配": True, "clear_image": True})
        text = f"Synthetic {request.material_type} document for 林晓安. File: {request.file_name}."
        return OCRResult(
            ocr_text=text,
            extracted_fields=fields,
            quality_notes=("Deterministic mock OCR was used; no external service was called.",),
            provider="mock",
        )
