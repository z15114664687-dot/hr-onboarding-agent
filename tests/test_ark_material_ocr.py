import httpx
import pytest

from app.core.config import get_settings
from app.services import ark_material_ocr


def test_ark_default_model_uses_lite() -> None:
    get_settings.cache_clear()
    try:
        assert get_settings().ark_model == "doubao-seed-2-0-lite-260428"
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_ark_material_ocr_parses_responses_output(monkeypatch) -> None:
    monkeypatch.setenv("ARK_API_KEY", "ark-key")
    monkeypatch.setenv("ARK_MODEL", "doubao-seed-2-0-pro-260215")
    get_settings.cache_clear()
    captured: dict[str, object] = {}

    async def fake_post(client, url, *, headers, json_payload):
        captured["url"] = url
        captured["headers"] = headers
        captured["payload"] = json_payload
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "content": [
                            {
                                "type": "output_text",
                                "text": '```json\n{"ocr_text":"姓名 张三","extracted_fields":{"姓名":"张三"},"quality_notes":[]}\n```',
                            }
                        ]
                    }
                ]
            },
        )

    monkeypatch.setattr(ark_material_ocr, "_post_with_retries", fake_post)
    try:
        result = await ark_material_ocr.extract_material_with_ark_content(
            file_name="证书.png",
            content=b"image",
            mime_type="image/png",
            material_type="degree_certificate",
        )
    finally:
        get_settings.cache_clear()

    assert result["ocr_text"] == "姓名 张三"
    assert result["extracted_fields"]["姓名"] == "张三"
    payload = captured["payload"]
    image_part = payload["input"][0]["content"][0]
    assert image_part["type"] == "input_image"
    assert image_part["image_url"].startswith("data:image/png;base64,")
    assert captured["url"] == "https://ark.cn-beijing.volces.com/api/v3/responses"


def test_ark_prompt_requests_resume_experience_fields() -> None:
    prompt = ark_material_ocr._ark_ocr_prompt("resume", "个人简历.pdf")

    assert "主动提取教育经历" in prompt
    assert "逐页判断图片/PDF方向" in prompt
    assert '"求学经历": []' in prompt
    assert '"工作经历": []' in prompt
    assert '"简历摘要": ""' in prompt
    assert '"发放时间": ""' in prompt


def test_ark_prompt_requests_degree_translation_and_orientation_fields() -> None:
    prompt = ark_material_ocr._ark_ocr_prompt("degree_certificate", "Degree.pdf")

    assert "如为英文或中英文证书" in prompt
    assert '"原文学校": ""' in prompt
    assert '"原文学位类别": ""' in prompt
    assert '"image_orientation_consistent": true' in prompt
    assert '"orientation_issue": ""' in prompt


@pytest.mark.asyncio
async def test_ark_requirement_compare_parses_matches(monkeypatch) -> None:
    monkeypatch.setenv("ARK_API_KEY", "ark-key")
    get_settings.cache_clear()
    captured: dict[str, object] = {}

    async def fake_post(client, url, *, headers, json_payload):
        captured["payload"] = json_payload
        return httpx.Response(
            200,
            json={"output_text": '{"matches":[{"label":"星河大学优秀学生","matched":true,"confidence":"high","reason":"名称和学校一致"}]}'},
        )

    monkeypatch.setattr(ark_material_ocr, "_post_with_retries", fake_post)
    try:
        result = await ark_material_ocr.compare_material_requirements_with_ark(
            material_type="award_certificate",
            candidate_name="林晓安",
            requirements=["星河大学优秀学生"],
            resume_context={"fields": {"获奖信息": ["星河大学优秀学生"]}},
            material_evidence={"current": {"fields": {"奖项名称": "优秀学生", "发证机构": "星河大学"}}},
        )
    finally:
        get_settings.cache_clear()

    prompt = captured["payload"]["input"][0]["content"][0]["text"]
    assert "奖项名称可以存在简称" in prompt
    assert result["matches"][0]["label"] == "星河大学优秀学生"


@pytest.mark.asyncio
async def test_ark_material_ocr_sends_pdf_as_input_file(monkeypatch) -> None:
    monkeypatch.setenv("ARK_API_KEY", "ark-key")
    get_settings.cache_clear()
    captured: dict[str, object] = {}

    async def fake_post(client, url, *, headers, json_payload):
        captured["payload"] = json_payload
        return httpx.Response(
            200,
            json={"output_text": '{"ocr_text":"PDF文字","extracted_fields":{},"quality_notes":[]}'},
        )

    monkeypatch.setattr(ark_material_ocr, "_post_with_retries", fake_post)
    try:
        result = await ark_material_ocr.extract_material_with_ark_content(
            file_name="材料.pdf",
            content=b"%PDF-1.4",
            mime_type="application/pdf",
            material_type="degree_certificate",
        )
    finally:
        get_settings.cache_clear()

    file_part = captured["payload"]["input"][0]["content"][0]
    assert file_part["type"] == "input_file"
    assert file_part["filename"] == "材料.pdf"
    assert file_part["file_data"].startswith("data:application/pdf;base64,")
    assert result["ocr_text"] == "PDF文字"


@pytest.mark.asyncio
async def test_ark_resume_uses_text_when_json_is_malformed(monkeypatch) -> None:
    monkeypatch.setenv("ARK_API_KEY", "ark-key")
    get_settings.cache_clear()

    async def fake_post(client, url, *, headers, json_payload):
        return httpx.Response(
            200,
            json={"output_text": "张三 个人简历 教育经历：某大学本科。工作经历：某公司实习。"},
        )

    monkeypatch.setattr(ark_material_ocr, "_post_with_retries", fake_post)
    try:
        result = await ark_material_ocr.extract_material_with_ark_content(
            file_name="个人简历.pdf",
            content=b"%PDF-1.4",
            mime_type="application/pdf",
            material_type="resume",
        )
    finally:
        get_settings.cache_clear()

    assert "个人简历" in result["ocr_text"]
    assert result["extracted_fields"]["材料类型匹配"] is True
    assert result["extracted_fields"]["简历摘要"]


@pytest.mark.asyncio
async def test_ark_material_ocr_reports_model_not_open(monkeypatch) -> None:
    monkeypatch.setenv("ARK_API_KEY", "ark-key")
    get_settings.cache_clear()

    async def fake_post(*args, **kwargs):
        return httpx.Response(
            404,
            json={"error": {"code": "ModelNotOpen", "message": "model not activated"}},
        )

    monkeypatch.setattr(ark_material_ocr, "_post_with_retries", fake_post)
    try:
        with pytest.raises(ark_material_ocr.ArkMaterialOcrError, match="not activated"):
            await ark_material_ocr.extract_material_with_ark_content(
                file_name="证书.png",
                content=b"image",
                mime_type="image/png",
                material_type="degree_certificate",
            )
    finally:
        get_settings.cache_clear()
