import pytest

from app.services import reminder_copy


@pytest.mark.asyncio
async def test_overdue_guidance_uses_stable_fallback_even_with_model_key(monkeypatch) -> None:
    monkeypatch.setenv("ZHIPU_API_KEY", "zhipu-key")

    async def fail_call(*args, **kwargs):
        raise AssertionError("overdue reminders should not call AI copy generation")

    monkeypatch.setattr(reminder_copy, "_call_zhipu_reminder_copy", fail_call)

    text = await reminder_copy.generate_overdue_guidance(
        candidate_name="林晓安",
        node_name="contract_signing",
        due_date="2026-06-04",
        overdue_days=3,
        audience="candidate",
        fallback="请按 HR 或电子签平台提示完成劳动合同签署；如已经签署，请在入职进度页点击“我已确认”。",
    )

    assert text == "请按 HR 或电子签平台提示完成劳动合同签署；如已经签署，请在入职进度页点击“我已确认”。"
