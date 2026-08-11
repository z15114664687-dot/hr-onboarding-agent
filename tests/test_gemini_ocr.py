import pytest

from app.core.config import get_settings
from app.services import gemini_ocr
from app.services.gemini_ocr import _ocr_prompt, _parse_json_response
from app.services.material_standards import get_material_standard, material_standard_prompt, material_visual_reference_prompt


def test_material_standard_prompt_includes_attachment_one_id_card_rules() -> None:
    prompt = material_standard_prompt("id_card")

    assert "正反面" in prompt
    assert "1:1" in prompt
    assert "彩色扫描" in prompt


def test_gemini_ocr_parse_json_fenced_response() -> None:
    parsed = _parse_json_response('```json\n{"ocr_text":"abc","extracted_fields":{"姓名":"张三"},"quality_notes":[]}\n```')

    assert parsed["ocr_text"] == "abc"
    assert parsed["extracted_fields"]["姓名"] == "张三"


def test_material_standards_cover_attachment_one_examples() -> None:
    for material_type in (
        "badge_photo",
        "id_card",
        "resume",
        "degree_certificate",
        "overseas_degree_certification",
        "hukou_material",
        "bank_card",
        "securities_statement",
        "resignation_certificate",
        "labor_termination_form",
        "employment_manual",
    ):
        assert get_material_standard(material_type) is not None


def test_gemini_ocr_prompt_requests_visual_material_checks() -> None:
    prompt = _ocr_prompt("degree_certificate", "证书.pdf")

    assert "不是只做 OCR" in prompt
    assert "检测到毕业证书" in prompt
    assert "检测到学位证书" in prompt
    assert "学信网报告" in prompt
    assert "逐页判断图片/PDF方向" in prompt
    assert '"image_orientation_consistent": true' in prompt
    assert '"原文学校": ""' in prompt


def test_gemini_ocr_prompt_requests_resume_extraction() -> None:
    prompt = _ocr_prompt("resume", "简历.pdf")

    assert "求职简历" in prompt
    assert "求学经历" in prompt
    assert "职称资格" in prompt


def test_visual_reference_prompt_mentions_icbc_without_name_requirement() -> None:
    prompt = material_visual_reference_prompt("bank_card")

    assert "工商银行" in prompt
    assert "姓名缺失" in prompt


@pytest.mark.asyncio
async def test_gemini_ocr_uses_gateway_without_local_api_key(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("GEMINI_GATEWAY_URL", "https://gateway.example.com")
    monkeypatch.setenv("GEMINI_GATEWAY_TOKEN", "token")
    get_settings.cache_clear()
    upload = tmp_path / "身份证.jpg"
    upload.write_bytes(b"image")
    called: dict[str, str] = {}

    async def fake_gateway(**kwargs):
        called.update({key: str(value) for key, value in kwargs.items() if key != "content"})
        return {"ocr_text": "姓名 张三", "extracted_fields": {"姓名": "张三"}, "quality_notes": []}

    monkeypatch.setattr(gemini_ocr, "_extract_material_with_gateway", fake_gateway)
    try:
        result = await gemini_ocr.extract_material_with_gemini(str(upload), "id_card")
    finally:
        get_settings.cache_clear()

    assert result["extracted_fields"]["姓名"] == "张三"
    assert called["material_type"] == "id_card"
    assert called["file_name"] == "身份证.jpg"


@pytest.mark.asyncio
async def test_gemini_ocr_prefers_gateway_when_local_api_key_is_configured(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "local-key")
    monkeypatch.setenv("GEMINI_GATEWAY_URL", "https://gateway.example.com")
    monkeypatch.setenv("GEMINI_GATEWAY_TOKEN", "token")
    get_settings.cache_clear()
    upload = tmp_path / "毕业证.jpg"
    upload.write_bytes(b"image")
    called = {"gateway": False, "direct": False}

    async def fake_gateway(**kwargs):
        called["gateway"] = True
        return {"ocr_text": "普通高等学校毕业证书", "extracted_fields": {"检测到毕业证书": True}, "quality_notes": []}

    async def fake_direct(**kwargs):
        called["direct"] = True
        return {"ocr_text": "", "extracted_fields": {}, "quality_notes": []}

    monkeypatch.setattr(gemini_ocr, "_extract_material_with_gateway", fake_gateway)
    monkeypatch.setattr(gemini_ocr, "extract_material_with_gemini_content", fake_direct)
    try:
        result = await gemini_ocr.extract_material_with_gemini(str(upload), "degree_certificate")
    finally:
        get_settings.cache_clear()

    assert result["extracted_fields"]["检测到毕业证书"] is True
    assert called == {"gateway": True, "direct": False}
