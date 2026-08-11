from datetime import date
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.models.base import Base
from app.schemas.onboarding import CandidateCaseCreate
from app.services.onboarding_workflow import create_candidate_case
from app.services.reminder_service import scan_overdue_and_notify


@pytest.mark.asyncio
async def test_scan_overdue_and_notify_is_idempotent_per_day(monkeypatch) -> None:
    monkeypatch.setenv("ZHIPU_API_KEY", "")
    get_settings.cache_clear()
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    sent: list[tuple[str, dict[str, Any]]] = []

    async def fake_send_card(open_id: str, card: dict[str, Any]) -> dict[str, Any]:
        sent.append((open_id, card))
        return {"code": 0}

    with session_factory() as db:
        create_candidate_case(
            db,
            CandidateCaseCreate(
                candidate_name="王五",
                feishu_open_id="ou_candidate",
                hrbp_open_id="ou_hrbp",
                manager_open_id="ou_manager",
                expected_onboard_date=date(2026, 6, 10),
            ),
        )

        first_result = await scan_overdue_and_notify(
            db,
            today=date(2026, 6, 20),
            send_card=fake_send_card,
            sync_bitable=False,
        )
        second_result = await scan_overdue_and_notify(
            db,
            today=date(2026, 6, 20),
            send_card=fake_send_card,
            sync_bitable=False,
        )

    assert first_result["overdue_count"] > 0
    assert first_result["notified_count"] > 0
    assert second_result["notified_count"] == 0
    assert second_result["skipped_count"] == second_result["overdue_count"]
    owner_cards = [card for open_id, card in sent if open_id == "ou_hrbp"]
    candidate_cards = [card for open_id, card in sent if open_id == "ou_candidate"]
    assert owner_cards
    assert candidate_cards
    assert "打开 HR 后台" in str(owner_cards[0])
    assert "打开 HR 后台" not in str(candidate_cards[0])
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_probation_email_reminder_goes_to_hr_with_manager_prompt(monkeypatch) -> None:
    monkeypatch.setenv("ZHIPU_API_KEY", "")
    get_settings.cache_clear()
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    sent: list[tuple[str, dict[str, Any]]] = []

    async def fake_send_card(open_id: str, card: dict[str, Any]) -> dict[str, Any]:
        sent.append((open_id, card))
        return {"code": 0}

    with session_factory() as db:
        case = create_candidate_case(
            db,
            CandidateCaseCreate(
                candidate_name="试用期提醒",
                department="投资研究部",
                hrbp_open_id="ou_hrbp",
                manager_open_id="ou_manager",
                expected_onboard_date=date(2026, 1, 1),
            ),
        )
        for node in case.nodes:
            if node.node_name != "probation_month_3":
                node.status = "submitted"
        db.commit()

        result = await scan_overdue_and_notify(
            db,
            today=date(2026, 4, 5),
            send_card=fake_send_card,
            sync_bitable=False,
        )

    assert result["overdue_count"] == 1
    assert result["notified_count"] == 1
    assert [open_id for open_id, _ in sent] == ["ou_hrbp"]
    content = sent[0][1]["elements"][0]["content"]
    assert "部门负责人" in content
    assert "投资研究部负责人（ou_manager）" in content
    assert "试用期考察邮件" in content
    assert "ou_manager" not in [open_id for open_id, _ in sent]
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_candidate_open_id_is_never_sent_hr_overdue_card(monkeypatch) -> None:
    monkeypatch.setenv("ZHIPU_API_KEY", "")
    get_settings.cache_clear()
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    sent: list[tuple[str, dict[str, Any]]] = []

    async def fake_send_card(open_id: str, card: dict[str, Any]) -> dict[str, Any]:
        sent.append((open_id, card))
        return {"code": 0}

    with session_factory() as db:
        create_candidate_case(
            db,
            CandidateCaseCreate(
                candidate_name="同一账号测试",
                feishu_open_id="ou_same",
                hrbp_open_id="ou_same",
                manager_open_id="ou_manager",
                expected_onboard_date=date(2026, 6, 10),
            ),
        )

        result = await scan_overdue_and_notify(
            db,
            today=date(2026, 6, 20),
            send_card=fake_send_card,
            sync_bitable=False,
        )

    assert result["overdue_count"] > 0
    assert sent
    same_open_id_cards = [card for open_id, card in sent if open_id == "ou_same"]
    assert same_open_id_cards
    assert "打开 HR 后台" not in str(same_open_id_cards)
    assert "进入工作台" in str(same_open_id_cards)
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_overdue_ai_guidance_is_generated_per_audience(monkeypatch) -> None:
    monkeypatch.setenv("ZHIPU_API_KEY", "")
    get_settings.cache_clear()
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    sent: list[tuple[str, dict[str, Any]]] = []
    audiences: list[str] = []

    async def fake_generate_guidance(**kwargs: Any) -> str:
        audience = str(kwargs["audience"])
        audiences.append(audience)
        return f"{audience} AI 生成提醒"

    async def fake_send_card(open_id: str, card: dict[str, Any]) -> dict[str, Any]:
        sent.append((open_id, card))
        return {"code": 0}

    monkeypatch.setattr("app.services.reminder_service.generate_overdue_guidance", fake_generate_guidance)

    with session_factory() as db:
        create_candidate_case(
            db,
            CandidateCaseCreate(
                candidate_name="AI话术测试",
                feishu_open_id="ou_candidate",
                hrbp_open_id="ou_hrbp",
                manager_open_id="ou_manager",
                expected_onboard_date=date(2026, 6, 10),
            ),
        )

        await scan_overdue_and_notify(
            db,
            today=date(2026, 6, 20),
            send_card=fake_send_card,
            sync_bitable=False,
        )

    assert "owner" in audiences
    assert "candidate" in audiences
    owner_content = next(card["elements"][0]["content"] for open_id, card in sent if open_id == "ou_hrbp")
    candidate_content = next(card["elements"][0]["content"] for open_id, card in sent if open_id == "ou_candidate")
    assert "owner AI 生成提醒" in owner_content
    assert "candidate AI 生成提醒" in candidate_content
    get_settings.cache_clear()
