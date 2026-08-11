import pytest

from app.core.config import get_settings
from app.schemas.onboarding import MaterialCheck, MaterialReviewResult
from app.services import gemini_material_judge
from app.services.gemini_material_judge import _merge_gemini_review, _parse_json_response


def test_gemini_judge_does_not_override_hard_rejection_to_approved() -> None:
    base = MaterialReviewResult(
        material_type="resignation_certificate",
        decision="auto_rejected",
        extracted_fields={"姓名": "张三"},
        checks=[MaterialCheck(rule="检测到盖章", status="fail", reason="未检测到盖章")],
        message_to_candidate="请重新上传。",
    )

    merged = _merge_gemini_review(
        base,
        {
            "decision": "auto_approved",
            "checks": [{"rule": "Gemini判断", "status": "pass", "reason": "看起来可以"}],
            "message_to_candidate": "已通过。",
            "hr_notes": "模型试图放行。",
        },
    )

    assert merged.decision == "auto_rejected"
    assert merged.message_to_candidate == "请重新上传。"
    assert any(check.rule == "检测到盖章" and check.status == "fail" for check in merged.checks)
    assert merged.review_source == "rules+gemini"
    assert merged.hr_notes == "模型试图放行。"


def test_gemini_judge_can_escalate_approved_to_manual_review() -> None:
    base = MaterialReviewResult(
        material_type="id_card",
        decision="auto_approved",
        extracted_fields={"姓名": "张三"},
        checks=[MaterialCheck(rule="姓名一致", status="pass", reason="姓名一致")],
        message_to_candidate="材料已通过自动审核。",
    )

    merged = _merge_gemini_review(
        base,
        {
            "decision": "manual_review",
            "checks": [{"rule": "图像质量", "status": "manual_review", "reason": "边缘略有裁切"}],
            "message_to_candidate": "材料已收到，需人工复核边缘裁切情况。",
        },
    )

    assert merged.decision == "manual_review"
    assert any(check.rule == "图像质量" for check in merged.checks)


def test_gemini_material_judge_parse_json_fence() -> None:
    parsed = _parse_json_response('```json\n{"decision":"manual_review","checks":[],"message_to_candidate":"需复核"}\n```')

    assert parsed["decision"] == "manual_review"


@pytest.mark.asyncio
async def test_gemini_material_judge_uses_gateway_without_local_api_key(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("GEMINI_GATEWAY_URL", "https://gateway.example.com")
    monkeypatch.setenv("GEMINI_GATEWAY_TOKEN", "token")
    get_settings.cache_clear()
    base = MaterialReviewResult(
        material_type="bank_card",
        decision="manual_review",
        extracted_fields={"银行名称": "中国工商银行"},
        checks=[MaterialCheck(rule="银行卡类型", status="manual_review", reason="需确认")],
        message_to_candidate="材料已收到。",
    )

    async def fake_gateway(**kwargs):
        assert kwargs["material_type"] == "bank_card"
        return MaterialReviewResult(
            material_type="bank_card",
            decision="auto_approved",
            extracted_fields={"银行名称": "中国工商银行"},
            checks=[MaterialCheck(rule="银行卡类型", status="pass", reason="已识别为工商银行卡")],
            message_to_candidate="工资卡已通过自动审核。",
            review_source="rules+gemini",
        )

    monkeypatch.setattr(gemini_material_judge, "_judge_material_with_gateway", fake_gateway)
    try:
        review = await gemini_material_judge.judge_material_with_gemini(
            material_type="bank_card",
            candidate_name="张三",
            ocr_text="中国工商银行",
            extracted_fields={"银行名称": "中国工商银行"},
            base_review=base,
        )
    finally:
        get_settings.cache_clear()

    assert review.decision == "auto_approved"
    assert review.review_source == "rules+gemini"


@pytest.mark.asyncio
async def test_gemini_material_judge_prefers_gateway_when_local_api_key_is_configured(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "local-key")
    monkeypatch.setenv("GEMINI_GATEWAY_URL", "https://gateway.example.com")
    monkeypatch.setenv("GEMINI_GATEWAY_TOKEN", "token")
    get_settings.cache_clear()
    base = MaterialReviewResult(
        material_type="bank_card",
        decision="manual_review",
        extracted_fields={"银行名称": "中国工商银行"},
        checks=[MaterialCheck(rule="银行卡类型", status="manual_review", reason="需确认")],
        message_to_candidate="材料已收到。",
    )
    called = {"gateway": False, "direct": False}

    async def fake_gateway(**kwargs):
        called["gateway"] = True
        return MaterialReviewResult(
            material_type="bank_card",
            decision="auto_approved",
            extracted_fields={"银行名称": "中国工商银行"},
            checks=[MaterialCheck(rule="银行卡类型", status="pass", reason="已识别为工商银行卡")],
            message_to_candidate="工资卡已通过自动审核。",
            review_source="rules+gemini",
        )

    async def fake_direct(**kwargs):
        called["direct"] = True
        return base

    monkeypatch.setattr(gemini_material_judge, "_judge_material_with_gateway", fake_gateway)
    monkeypatch.setattr(gemini_material_judge, "judge_material_with_gemini_direct", fake_direct)
    try:
        review = await gemini_material_judge.judge_material_with_gemini(
            material_type="bank_card",
            candidate_name="张三",
            ocr_text="中国工商银行",
            extracted_fields={"银行名称": "中国工商银行"},
            base_review=base,
        )
    finally:
        get_settings.cache_clear()

    assert review.decision == "auto_approved"
    assert called == {"gateway": True, "direct": False}
