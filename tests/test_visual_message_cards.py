from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.services.qa_service import QAResult
from app.schemas.onboarding import CandidateCaseCreate
from app.services import feishu_message_service
from app.services.feishu_message_service import route_text_message_response, try_send_visual_command
from app.services.onboarding_workflow import create_candidate_case


@pytest.mark.asyncio
async def test_progress_command_sends_visual_card(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    sent: list[dict[str, Any]] = []

    async def fake_send_card_reply(open_id: str, chat_id: str | None, chat_type: str | None, card: dict[str, Any]) -> None:
        sent.append(card)

    monkeypatch.setattr(feishu_message_service, "send_card_reply", fake_send_card_reply)

    with session_factory() as db:
        create_candidate_case(
            db,
            CandidateCaseCreate(
                candidate_name="赵六",
                feishu_open_id="ou_visual",
                employment_type="社招",
                expected_onboard_date=date(2026, 6, 1),
            ),
        )

        handled = await try_send_visual_command("进度", open_id="ou_visual", chat_id=None, chat_type=None, db=db)

    assert handled is True
    assert sent
    assert sent[0]["header"]["title"]["content"] == "入职进度"
    assert _card_actions(sent[0])[0]["text"]["content"] == "进入入职工作台"


@pytest.mark.asyncio
async def test_material_command_sends_visual_card(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    sent: list[dict[str, Any]] = []

    async def fake_send_card_reply(open_id: str, chat_id: str | None, chat_type: str | None, card: dict[str, Any]) -> None:
        sent.append(card)

    monkeypatch.setattr(feishu_message_service, "send_card_reply", fake_send_card_reply)

    with session_factory() as db:
        create_candidate_case(
            db,
            CandidateCaseCreate(
                candidate_name="钱七",
                feishu_open_id="ou_material_visual",
                employment_type="社招",
                expected_onboard_date=date(2026, 6, 1),
            ),
        )

        handled = await try_send_visual_command("材料", open_id="ou_material_visual", chat_id=None, chat_type=None, db=db)

    assert handled is True
    assert sent
    assert sent[0]["header"]["title"]["content"] == "材料提交清单"
    assert "离职证明" in str(sent[0]["elements"])
    assert _card_actions(sent[0])[0]["text"]["content"] == "进入入职工作台"


@pytest.mark.asyncio
async def test_visual_command_rebinds_open_id_by_union_id(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    sent: list[dict[str, Any]] = []

    async def fake_send_card_reply(open_id: str, chat_id: str | None, chat_type: str | None, card: dict[str, Any]) -> None:
        sent.append(card)

    monkeypatch.setattr(feishu_message_service, "send_card_reply", fake_send_card_reply)

    with session_factory() as db:
        case = create_candidate_case(
            db,
            CandidateCaseCreate(
                candidate_name="孙八",
                feishu_open_id="ou_old",
                feishu_union_id="on_same_user",
                employment_type="社招",
                expected_onboard_date=date(2026, 6, 1),
            ),
        )

        await feishu_message_service.handle_message_event(
            {
                "message": {
                    "message_id": "om_rebind",
                    "message_type": "text",
                    "chat_type": "p2p",
                    "content": {"text": "进度"},
                },
                "sender": {"sender_id": {"open_id": "ou_new", "union_id": "on_same_user"}},
            },
            db=db,
        )

        db.refresh(case)

    assert sent
    assert sent[0]["header"]["title"]["content"] == "入职进度"
    assert case.feishu_open_id == "ou_new"


@pytest.mark.asyncio
async def test_general_qa_message_returns_interactive_card(monkeypatch) -> None:
    async def fake_answer_hr_question(_text: str) -> QAResult:
        return QAResult(
            answer="**北京** 当前按政策口径执行。",
            sources=[{"id": "S1", "title": "北京福利政策", "path": "/policy/beijing.md"}],
        )

    monkeypatch.setattr(feishu_message_service, "answer_hr_question", fake_answer_hr_question)

    response = await route_text_message_response("北京福利标准是什么", "ou_qa", db=None)  # type: ignore[arg-type]

    assert response["type"] == "card"
    assert response["card"]["header"]["title"]["content"] == "智能问答"
    assert "**北京**" in response["card"]["elements"][0]["content"]


@pytest.mark.asyncio
async def test_general_qa_message_passes_candidate_tags(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    captured_tags: list[dict[str, str | list[str]] | None] = []

    async def fake_answer_hr_question(_text: str, *, user_tags=None) -> QAResult:
        captured_tags.append(user_tags)
        return QAResult(answer="正式员工福利需按身份过滤。", sources=[])

    monkeypatch.setattr(feishu_message_service, "answer_hr_question", fake_answer_hr_question)

    with session_factory() as db:
        create_candidate_case(
            db,
            CandidateCaseCreate(
                candidate_name="标签候选人",
                feishu_open_id="ou_candidate_tags",
                employment_type="校招",
                department="产品与技术",
                position="研究员",
                job_level="P2",
                city="上海",
                expected_onboard_date=date(2026, 6, 1),
            ),
        )

        response = await route_text_message_response("正式员工福利有哪些", "ou_candidate_tags", db)

    assert response["type"] == "card"
    assert captured_tags
    assert captured_tags[0]["employee_status"] == ["candidate", "准员工", "候选人"]
    assert captured_tags[0]["department"] == "产品与技术"
    assert captured_tags[0]["city"] == "上海"


@pytest.mark.asyncio
async def test_general_qa_message_passes_owner_formal_tags(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    captured_tags: list[dict[str, str | list[str]] | None] = []

    async def fake_answer_hr_question(_text: str, *, user_tags=None) -> QAResult:
        captured_tags.append(user_tags)
        return QAResult(answer="负责人可看正式员工福利。", sources=[])

    monkeypatch.setattr(feishu_message_service, "answer_hr_question", fake_answer_hr_question)

    with session_factory() as db:
        create_candidate_case(
            db,
            CandidateCaseCreate(
                candidate_name="负责人标签",
                manager_open_id="ou_manager_tags",
                employment_type="校招",
                department="技术条线",
                position="工程师",
                job_level="P5",
                city="北京",
                expected_onboard_date=date(2026, 6, 1),
            ),
        )

        response = await route_text_message_response("正式员工福利有哪些", "ou_manager_tags", db)

    assert response["type"] == "card"
    assert captured_tags
    assert captured_tags[0]["employee_status"] == ["formal_employee", "regular_employee", "正式员工", "正职员工"]
    assert captured_tags[0]["department"] == "技术条线"
    assert captured_tags[0]["job_level"] == "P5"


@pytest.mark.asyncio
async def test_qa_card_failure_falls_back_to_text(monkeypatch) -> None:
    async def fake_answer_hr_question(_text: str) -> QAResult:
        return QAResult(
            answer="北京福利标准按本地政策执行。",
            sources=[{"id": "S1", "title": "北京福利政策", "path": "/policy/beijing.md"}],
        )

    async def failing_send_card_reply(
        open_id: str,
        chat_id: str | None,
        chat_type: str | None,
        card: dict[str, Any],
    ) -> None:
        raise RuntimeError("card send failed")

    sent_text: list[str] = []

    async def fake_send_reply(open_id: str, chat_id: str | None, chat_type: str | None, text: str) -> None:
        sent_text.append(text)

    monkeypatch.setattr(feishu_message_service, "answer_hr_question", fake_answer_hr_question)
    monkeypatch.setattr(feishu_message_service, "send_card_reply", failing_send_card_reply)
    monkeypatch.setattr(feishu_message_service, "send_reply", fake_send_reply)

    await feishu_message_service.handle_message_event(
        {
            "message": {
                "message_id": "om_qa",
                "message_type": "text",
                "chat_type": "p2p",
                "content": {"text": "北京福利标准是什么"},
            },
            "sender": {"sender_id": {"open_id": "ou_qa"}},
        },
        db=None,  # type: ignore[arg-type]
    )

    assert sent_text
    assert "北京福利标准按本地政策执行" in sent_text[0]
    assert "引用来源" in sent_text[0]


def _card_actions(card: dict[str, Any]) -> list[dict[str, Any]]:
    return next(element["actions"] for element in card["elements"] if element.get("tag") == "action")
