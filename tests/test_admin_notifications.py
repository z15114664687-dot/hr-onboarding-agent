from datetime import date

import pytest

from app.api.routes import admin
from app.models.onboarding import CandidateCase


@pytest.mark.asyncio
async def test_notify_candidate_welcome_sends_plain_text(monkeypatch) -> None:
    sent: dict[str, str] = {}

    async def fake_send_text(open_id: str, text: str) -> dict:
        sent["open_id"] = open_id
        sent["text"] = text
        return {"code": 0, "data": {"message_id": "om_text"}}

    async def fail_send_card(*args, **kwargs):
        raise AssertionError("welcome message should not be sent as an interactive card")

    monkeypatch.setattr(admin, "send_text", fake_send_text)
    monkeypatch.setattr(admin, "send_interactive_card", fail_send_card)

    case = CandidateCase(
        id=7,
        candidate_name="张三",
        feishu_open_id="ou_candidate",
        expected_onboard_date=date(2026, 6, 8),
    )

    result = await admin._notify_candidate_welcome(case)

    assert result["status"] == "success"
    assert sent["open_id"] == "ou_candidate"
    assert "张三你好" in sent["text"]
    assert "/ui/candidate/7" in sent["text"]
