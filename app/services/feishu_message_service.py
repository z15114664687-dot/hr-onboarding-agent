from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from app.adapters.feishu_im import (
    material_request_card,
    progress_card,
    qa_answer_card,
    send_interactive_card,
    send_interactive_card_to_chat,
    send_text,
    send_text_to_chat,
)
from app.models.onboarding import CandidateCase, MaterialSubmission, WorkflowNode
from app.services import faq_service
from app.services.material_catalog import format_materials_message, normalize_employment_type, required_materials_for_employment_type
from app.services.onboarding_workflow import (
    NODE_DISPLAY_NAMES,
    NODE_STATUS_DISPLAY_NAMES,
    candidate_current_node,
    candidate_visible_nodes,
    get_progress_for_open_id,
)
from app.services.qa_service import answer_hr_question, format_qa_reply
from app.utils.feishu_links import candidate_materials_url, candidate_node_confirm_url, candidate_page_url
from app.utils.idempotency import claim_event, mark_event_status

logger = logging.getLogger(__name__)

HELP_TEXT = "我可以帮你看入职进度、材料清单和常见 HR 问题。你可以直接发送：\n- 进度 / 我的入职进度\n- 材料 / 提交材料\n- 帮助 / help"


async def process_feishu_message_event(
    db: Session,
    *,
    external_event_id: str,
    event_type: str,
    event: dict[str, Any],
    raw_payload: dict[str, Any],
    source: str = "feishu_event",
) -> bool:
    """Process a Feishu message event with idempotency and reply handling."""

    if not claim_event(
        db,
        external_event_id=external_event_id,
        event_type=event_type,
        payload=raw_payload,
        source=source,
    ):
        logger.info("feishu_event_duplicate")
        return False

    try:
        await handle_message_event(event, db)
        mark_event_status(db, external_event_id=external_event_id, status="success")
        return True
    except Exception as exc:
        mark_event_status(db, external_event_id=external_event_id, status="failed", error_message=type(exc).__name__)
        raise


async def handle_message_event(event: dict[str, Any], db: Session) -> None:
    """Handle the Feishu im.message.receive_v1 event body."""

    message = event.get("message") or {}
    sender = event.get("sender") or {}
    chat_type = message.get("chat_type")
    chat_id = message.get("chat_id")
    sender_id = sender.get("sender_id") or {}
    open_id = sender_id.get("open_id") or ((message.get("sender") or {}).get("id"))
    union_id = sender_id.get("union_id")
    if not open_id:
        logger.info("feishu_message_missing_open_id")
        return
    if message.get("message_type") != "text":
        await send_reply(
            open_id=open_id,
            chat_id=chat_id,
            chat_type=chat_type,
            text="当前仅支持文本消息，请发送“帮助”查看可用指令。",
        )
        return

    text = clean_message_text(extract_text(message.get("content")), message.get("mentions") or [])
    logger.info("feishu_message_received chat_type=%s", chat_type)
    if await try_send_visual_command(text, open_id=open_id, union_id=union_id, chat_id=chat_id, chat_type=chat_type, db=db):
        return
    response = await route_text_message_response(text, open_id, db, union_id=union_id)
    logger.info(
        "feishu_message_routed response_type=%s",
        response.get("type"),
    )
    if response["type"] == "card":
        try:
            await send_card_reply(open_id=open_id, chat_id=chat_id, chat_type=chat_type, card=response["card"])
            logger.info("feishu_card_reply_sent chat_type=%s", chat_type)
        except Exception:
            fallback_text = str(response.get("text") or "")
            if not fallback_text:
                raise
            logger.warning("feishu_card_reply_failed")
            await send_reply(open_id=open_id, chat_id=chat_id, chat_type=chat_type, text=fallback_text)
            logger.info("feishu_text_fallback_sent chat_type=%s", chat_type)
        return
    await send_reply(open_id=open_id, chat_id=chat_id, chat_type=chat_type, text=response["text"])
    logger.info("feishu_text_reply_sent chat_type=%s", chat_type)


async def send_reply(open_id: str, chat_id: str | None, chat_type: str | None, text: str) -> None:
    """Reply to group messages in the group, otherwise reply to the sender."""

    if chat_type == "group" and chat_id:
        await send_text_to_chat(chat_id, text)
        return
    await send_text(open_id, text)


async def send_card_reply(open_id: str, chat_id: str | None, chat_type: str | None, card: dict[str, Any]) -> None:
    """Reply with an interactive card."""

    if chat_type == "group" and chat_id:
        await send_interactive_card_to_chat(chat_id, card)
        return
    await send_interactive_card(open_id, card)


async def try_send_visual_command(
    text: str,
    *,
    open_id: str,
    union_id: str | None = None,
    chat_id: str | None,
    chat_type: str | None,
    db: Session,
) -> bool:
    """Send visual cards for core onboarding commands when the user is bound to a case."""

    normalized = text.strip().lower()
    if normalized not in {"进度", "我的入职进度", "材料", "提交材料"}:
        return False
    case = _latest_case_for_open_id(db, open_id, union_id)
    if not case:
        await send_reply(
            open_id=open_id,
            chat_id=chat_id,
            chat_type=chat_type,
            text="暂未找到你的入职记录，请联系 HR 确认飞书账号是否已绑定。",
        )
        return True

    detail_url = candidate_page_url(case.id)
    material_url = candidate_materials_url(case.id)
    if normalized in {"进度", "我的入职进度"}:
        progress = _progress_card_payload(case)
        confirm_node_name = progress.get("confirm_node_name")
        card = progress_card(
            progress,
            detail_url=detail_url,
            material_url=material_url,
            confirm_url=candidate_node_confirm_url(case.id, str(confirm_node_name)) if confirm_node_name else None,
        )
    else:
        card = material_request_card(
            _material_card_lines(case),
            candidate_name=case.candidate_name,
            employment_type=_employment_type_label(case.employment_type),
            detail_url=detail_url,
            submit_url=material_url,
        )
    await send_card_reply(open_id=open_id, chat_id=chat_id, chat_type=chat_type, card=card)
    return True


async def route_text_message(text: str, open_id: str, db: Session, union_id: str | None = None) -> str:
    """Route a plain text command to onboarding, FAQ, or manual handoff."""

    response = await route_text_message_response(text, open_id, db, union_id=union_id)
    return str(response.get("text") or "")


async def route_text_message_response(text: str, open_id: str, db: Session, union_id: str | None = None) -> dict[str, Any]:
    """Route a plain text command and return either a text or card response."""

    normalized = text.strip().lower()
    if normalized in {"进度", "我的入职进度"}:
        return {"type": "text", "text": get_progress_for_open_id(db, open_id, union_id)}
    if normalized in {"材料", "提交材料"}:
        return {"type": "text", "text": get_materials_for_open_id(db, open_id, union_id)}
    if normalized in {"帮助", "help"}:
        return {"type": "text", "text": HELP_TEXT}
    faq_answer = faq_service.answer_question(text)
    if faq_answer:
        return {
            "type": "card",
            "card": qa_answer_card(question=text, answer=faq_answer),
            "text": faq_answer,
        }
    user_tags = _qa_user_tags_for_open_id(db, open_id, union_id)
    result = await answer_hr_question(text, user_tags=user_tags) if user_tags else await answer_hr_question(text)
    return {
        "type": "card",
        "card": qa_answer_card(
            question=text,
            answer=result.answer,
            sources=result.sources,
            used_external_search=result.used_external_search,
            needs_handoff=result.needs_handoff,
        ),
        "text": format_qa_reply(result),
    }


def get_materials_for_open_id(db: Session, open_id: str, union_id: str | None = None) -> str:
    """Return the candidate-specific material checklist when the open_id is bound."""

    case = (
        db.query(CandidateCase)
        .filter(_feishu_user_filter(open_id, union_id))
        .order_by(CandidateCase.created_at.desc())
        .first()
    )
    if not case:
        return format_materials_message(None)
    _refresh_open_id_if_needed(db, case, open_id)
    return format_materials_message(case.employment_type)


def _latest_case_for_open_id(db: Session, open_id: str, union_id: str | None = None) -> CandidateCase | None:
    case = (
        db.query(CandidateCase)
        .options(selectinload(CandidateCase.nodes), selectinload(CandidateCase.materials))
        .filter(_feishu_user_filter(open_id, union_id))
        .order_by(CandidateCase.created_at.desc())
        .first()
    )
    if case:
        _refresh_open_id_if_needed(db, case, open_id)
    return case


def _qa_user_tags_for_open_id(
    db: Session | None,
    open_id: str,
    union_id: str | None = None,
) -> dict[str, str | list[str]] | None:
    if db is None:
        return None
    case = _latest_case_for_open_id(db, open_id, union_id)
    if case:
        return _case_knowledge_tags(case, ["candidate", "准员工", "候选人"])

    owner_case = (
        db.query(CandidateCase)
        .join(WorkflowNode, WorkflowNode.case_id == CandidateCase.id, isouter=True)
        .filter(
            or_(
                CandidateCase.hrbp_open_id == open_id,
                CandidateCase.manager_open_id == open_id,
                WorkflowNode.owner_open_id == open_id,
                WorkflowNode.collaborator_open_id == open_id,
            )
        )
        .order_by(CandidateCase.created_at.desc())
        .first()
    )
    if owner_case:
        return _case_knowledge_tags(owner_case, ["formal_employee", "regular_employee", "正式员工", "正职员工"])
    return None


def _case_knowledge_tags(case: CandidateCase, employee_status: list[str]) -> dict[str, str | list[str]]:
    tags: dict[str, str | list[str]] = {
        "employee_status": employee_status,
        "audience": employee_status,
    }
    for field_name in ("employment_type", "city", "department", "position", "job_level"):
        value = getattr(case, field_name, None)
        if value:
            tags[field_name] = str(value)
    return tags


def _feishu_user_filter(open_id: str, union_id: str | None = None):
    if union_id:
        return or_(CandidateCase.feishu_open_id == open_id, CandidateCase.feishu_union_id == union_id)
    return CandidateCase.feishu_open_id == open_id


def _refresh_open_id_if_needed(db: Session, case: CandidateCase, open_id: str) -> None:
    if case.feishu_open_id == open_id:
        return
    logger.info("feishu_candidate_rebind case_id=%s", case.id)
    case.feishu_open_id = open_id
    db.add(case)
    db.commit()
    db.refresh(case)


def _progress_card_payload(case: CandidateCase) -> dict[str, Any]:
    nodes = sorted(candidate_visible_nodes(case.nodes), key=lambda node: node.due_date)
    pending_nodes = [node for node in nodes if node.status not in {"approved", "skipped", "submitted"}]
    next_node = pending_nodes[0] if pending_nodes else None
    current_node = candidate_current_node(case, nodes)
    return {
        "current_stage": NODE_DISPLAY_NAMES.get(current_node.node_name, current_node.node_name) if current_node else NODE_DISPLAY_NAMES.get(case.current_stage, case.current_stage),
        "overall_status": _status_label(case.overall_status),
        "next_step": NODE_DISPLAY_NAMES.get(next_node.node_name, next_node.node_name) if next_node else "全部完成",
        "next_due_date": next_node.due_date.isoformat() if next_node else "-",
        "approved_count": sum(1 for node in nodes if node.status in {"approved", "skipped", "submitted"}),
        "total_count": len(nodes),
        "confirm_node_name": next_node.node_name if next_node else None,
        "nodes": [_node_line_payload(node) for node in nodes],
    }


def _node_line_payload(node: WorkflowNode) -> dict[str, str]:
    return {
        "node_name": NODE_DISPLAY_NAMES.get(node.node_name, node.node_name),
        "status": _status_label(node.status),
        "due_date": node.due_date.isoformat(),
    }


def _material_card_lines(case: CandidateCase) -> list[str]:
    latest = _latest_materials_by_type(case.materials)
    lines: list[str] = []
    for item in required_materials_for_employment_type(case.employment_type):
        if not item.upload_required:
            continue
        submission = latest.get(item.code)
        status = _status_label(submission.review_status) if submission else "待提交"
        lines.append(f"{item.name}：{status}")
    return lines


def _latest_materials_by_type(materials: list[MaterialSubmission]) -> dict[str, MaterialSubmission]:
    latest: dict[str, MaterialSubmission] = {}
    for material in sorted(materials, key=lambda item: item.created_at):
        latest[material.material_type] = material
    return latest


def _employment_type_label(employment_type: str | None) -> str:
    return {
        "campus_domestic": "校招-境内",
        "campus_overseas": "校招-境外",
        "social": "社招",
    }[normalize_employment_type(employment_type)]


def _status_label(status: str) -> str:
    return {
        **NODE_STATUS_DISPLAY_NAMES,
        "pending": "进行中",
        "in_progress": "进行中",
        "risk": "有风险",
        "completed": "已完成",
        "terminated": "已终止",
        "auto_approved": "已通过",
        "auto_rejected": "已驳回",
        "manual_review": "人工复核",
        "approved": "已通过",
        "rejected": "已驳回",
    }.get(status, status)


def extract_text(content: Any) -> str:
    """Extract text from Feishu message content."""

    if isinstance(content, dict):
        return str(content.get("text", ""))
    if isinstance(content, str):
        try:
            return str(json.loads(content).get("text", ""))
        except json.JSONDecodeError:
            return content
    return ""


def clean_message_text(text: str, mentions: list[dict[str, Any]]) -> str:
    """Remove Feishu mention placeholders before command routing."""

    cleaned = text
    for mention in mentions:
        key = mention.get("key")
        if key:
            cleaned = cleaned.replace(str(key), "")
    return cleaned.strip()


def get_external_event_id(header: dict[str, Any], event: dict[str, Any]) -> str:
    """Resolve a stable idempotency key from Feishu event payload."""

    message = event.get("message") or {}
    return (
        header.get("event_id")
        or message.get("message_id")
        or header.get("create_time")
        or "unknown-event"
    )
