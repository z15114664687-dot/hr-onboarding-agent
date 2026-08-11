from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.adapters.feishu_chat import add_chat_members, create_onboarding_chat, extract_chat_id
from app.adapters.feishu_im import onboarding_chat_intro_card, send_interactive_card_to_chat
from app.models.onboarding import CandidateCase
from app.utils.feishu_links import candidate_page_url

logger = logging.getLogger(__name__)


def onboarding_chat_name(case: CandidateCase) -> str:
    """Return the default Feishu group name for a candidate onboarding case."""

    return f"{case.candidate_name} 入职协同群"


async def ensure_onboarding_chat(case: CandidateCase, db: Session, *, force_add_members: bool = False) -> dict[str, Any]:
    """Create or update the Feishu onboarding group for a candidate case."""

    missing = _missing_required_open_ids(case)
    if missing:
        return {"status": "skipped", "reason": f"缺少{'、'.join(missing)} open_id，无法创建入职协同群。"}

    members = _case_member_open_ids(case)
    if not members:
        return {"status": "skipped", "reason": "candidate, HRBP and manager open_id are all missing"}

    if case.feishu_chat_id:
        if force_add_members:
            add_result = await add_chat_members(case.feishu_chat_id, user_open_ids=members)
            return {"status": "success", "chat_id": case.feishu_chat_id, "action": "members_added", "members": members, "result": add_result}
        return {"status": "skipped", "reason": "chat already exists", "chat_id": case.feishu_chat_id, "members": members}

    response = await create_onboarding_chat(
        name=onboarding_chat_name(case),
        owner_open_id=case.hrbp_open_id or case.manager_open_id,
        user_open_ids=members,
        include_bot=True,
        description=f"{case.candidate_name} 的入职协同群，用于 HR、负责人、候选人和机器人同步入职进度。",
    )
    chat_id = extract_chat_id(response)
    if not chat_id:
        logger.warning("feishu_chat_created_without_chat_id case_id=%s", case.id)
        return {"status": "failed", "reason": "Feishu response did not include chat_id", "result": response}

    case.feishu_chat_id = chat_id
    db.add(case)
    db.commit()
    intro_result = await _send_chat_intro(case, chat_id)
    return {
        "status": "success",
        "chat_id": chat_id,
        "action": "created",
        "members": members,
        "result": response,
        "intro_message": intro_result,
    }


def _case_member_open_ids(case: CandidateCase) -> list[str]:
    candidates = [case.feishu_open_id, case.hrbp_open_id, case.manager_open_id]
    members: list[str] = []
    for open_id in candidates:
        value = str(open_id or "").strip()
        if value and value not in members:
            members.append(value)
    return members


def _missing_required_open_ids(case: CandidateCase) -> list[str]:
    missing = []
    if not str(case.feishu_open_id or "").strip():
        missing.append("候选人")
    if not (str(case.hrbp_open_id or "").strip() or str(case.manager_open_id or "").strip()):
        missing.append("HRBP/负责人")
    return missing


async def _send_chat_intro(case: CandidateCase, chat_id: str) -> dict[str, Any]:
    card = onboarding_chat_intro_card(
        case.candidate_name,
        str(case.expected_onboard_date or "-"),
        detail_url=candidate_page_url(case.id),
    )
    try:
        result = await send_interactive_card_to_chat(chat_id, card)
        return {"status": "success", "result": result}
    except Exception as exc:
        logger.warning("onboarding_chat_intro_failed case_id=%s error_type=%s", case.id, type(exc).__name__)
        return {"status": "failed", "reason": type(exc).__name__}
