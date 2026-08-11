from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.models.onboarding import CandidateCase
from app.services import onboarding_chat


@pytest.mark.asyncio
async def test_ensure_onboarding_chat_creates_and_stores_chat_id(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    calls: list[dict] = []

    async def fake_create_onboarding_chat(**kwargs):
        calls.append(kwargs)
        return {"code": 0, "data": {"chat_id": "oc_created"}}

    async def fake_send_intro(chat_id, card):
        calls.append({"chat_id": chat_id, "card": card})
        return {"code": 0, "data": {"message_id": "om_intro"}}

    monkeypatch.setattr(onboarding_chat, "create_onboarding_chat", fake_create_onboarding_chat)
    monkeypatch.setattr(onboarding_chat, "send_interactive_card_to_chat", fake_send_intro)

    with session_factory() as db:
        case = CandidateCase(
            candidate_name="张三",
            feishu_open_id="ou_candidate",
            hrbp_open_id="ou_hr",
            manager_open_id="ou_manager",
            expected_onboard_date=date(2026, 6, 8),
        )
        db.add(case)
        db.commit()

        result = await onboarding_chat.ensure_onboarding_chat(case, db)

        assert result["status"] == "success"
        assert result["chat_id"] == "oc_created"
        assert case.feishu_chat_id == "oc_created"
        assert calls[0]["owner_open_id"] == "ou_hr"
        assert calls[0]["user_open_ids"] == ["ou_candidate", "ou_hr", "ou_manager"]
        assert calls[1]["chat_id"] == "oc_created"
        assert result["intro_message"]["status"] == "success"


@pytest.mark.asyncio
async def test_ensure_onboarding_chat_skips_existing_without_force(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    async def unexpected_create_onboarding_chat(**kwargs):
        raise AssertionError("should not create a second chat")

    monkeypatch.setattr(onboarding_chat, "create_onboarding_chat", unexpected_create_onboarding_chat)

    with session_factory() as db:
        case = CandidateCase(
            candidate_name="张三",
            feishu_open_id="ou_candidate",
            hrbp_open_id="ou_hr",
            manager_open_id="ou_manager",
            feishu_chat_id="oc_existing",
            expected_onboard_date=date(2026, 6, 8),
        )
        db.add(case)
        db.commit()

        result = await onboarding_chat.ensure_onboarding_chat(case, db)

        assert result["status"] == "skipped"
        assert result["chat_id"] == "oc_existing"


@pytest.mark.asyncio
async def test_ensure_onboarding_chat_requires_candidate_and_owner_open_ids(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    async def unexpected_create_onboarding_chat(**kwargs):
        raise AssertionError("should not create a chat with missing required open_id")

    monkeypatch.setattr(onboarding_chat, "create_onboarding_chat", unexpected_create_onboarding_chat)

    with session_factory() as db:
        case = CandidateCase(candidate_name="张三", hrbp_open_id="ou_hr", expected_onboard_date=date(2026, 6, 8))
        db.add(case)
        db.commit()

        result = await onboarding_chat.ensure_onboarding_chat(case, db)

        assert result["status"] == "skipped"
        assert "缺少候选人 open_id" in result["reason"]


@pytest.mark.asyncio
async def test_ensure_onboarding_chat_keeps_chat_when_intro_fails(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    async def fake_create_onboarding_chat(**kwargs):
        return {"code": 0, "data": {"chat_id": "oc_created"}}

    async def fake_send_intro(chat_id, card):
        raise RuntimeError("message permission missing")

    monkeypatch.setattr(onboarding_chat, "create_onboarding_chat", fake_create_onboarding_chat)
    monkeypatch.setattr(onboarding_chat, "send_interactive_card_to_chat", fake_send_intro)

    with session_factory() as db:
        case = CandidateCase(
            candidate_name="张三",
            feishu_open_id="ou_candidate",
            hrbp_open_id="ou_hr",
            expected_onboard_date=date(2026, 6, 8),
        )
        db.add(case)
        db.commit()

        result = await onboarding_chat.ensure_onboarding_chat(case, db)

        assert result["status"] == "success"
        assert result["chat_id"] == "oc_created"
        assert result["intro_message"]["status"] == "failed"
        assert result["intro_message"]["reason"] == "RuntimeError"
