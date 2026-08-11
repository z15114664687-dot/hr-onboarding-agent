from __future__ import annotations

from typing import Any
import json
import re

import httpx

from app.adapters.feishu_auth import FEISHU_API_BASE, get_tenant_access_token
from app.adapters.feishu_http import request_with_retry
from app.services.material_standards import get_material_standard
from app.services.onboarding_workflow import NODE_DISPLAY_NAMES


class FeishuMessageError(RuntimeError):
    """Raised when Feishu accepts HTTP but rejects the message payload."""


async def send_text(open_id: str, text: str) -> dict[str, Any]:
    """Send a Feishu text message to an open_id."""

    return await _send_message(
        receive_id=open_id,
        receive_id_type="open_id",
        msg_type="text",
        content={"text": text},
    )


async def send_text_to_chat(chat_id: str, text: str) -> dict[str, Any]:
    """Send a Feishu text message to a chat_id."""

    return await _send_message(
        receive_id=chat_id,
        receive_id_type="chat_id",
        msg_type="text",
        content={"text": text},
    )


async def send_interactive_card(open_id: str, card_json: dict[str, Any]) -> dict[str, Any]:
    """Send a Feishu interactive card to an open_id."""

    return await _send_message(
        receive_id=open_id,
        receive_id_type="open_id",
        msg_type="interactive",
        content=card_json,
    )


async def send_interactive_card_to_chat(chat_id: str, card_json: dict[str, Any]) -> dict[str, Any]:
    """Send a Feishu interactive card to a chat_id."""

    return await _send_message(
        receive_id=chat_id,
        receive_id_type="chat_id",
        msg_type="interactive",
        content=card_json,
    )


async def _send_message(
    receive_id: str,
    receive_id_type: str,
    msg_type: str,
    content: dict[str, Any],
) -> dict[str, Any]:
    token = await get_tenant_access_token()
    async with httpx.AsyncClient(base_url=FEISHU_API_BASE, timeout=10) as client:
        response = await request_with_retry(
            client,
            "POST",
            "/open-apis/im/v1/messages",
            params={"receive_id_type": receive_id_type},
            headers={"Authorization": f"Bearer {token}"},
            json={
                "receive_id": receive_id,
                "msg_type": msg_type,
                "content": json.dumps(content, ensure_ascii=False),
            },
        )
        status_code = getattr(response, "status_code", 200)
        if status_code >= 400:
            raise FeishuMessageError(f"Feishu message HTTP error: status={status_code}")
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 0:
            raise FeishuMessageError(f"Feishu message send failed: code={data.get('code')}")
        return data


def welcome_card(
    candidate_name: str,
    expected_onboard_date: str,
    *,
    detail_url: str | None = None,
    material_url: str | None = None,
) -> dict[str, Any]:
    """Build a candidate-facing onboarding welcome card JSON."""

    content = (
        f"欢迎 {candidate_name}，你的入职流程已创建。\n"
        f"**预计入职日期**：{expected_onboard_date}\n\n"
        "我可以帮你查看入职进度、提交材料和确认当前节点。你也可以直接发送“进度”“材料”“帮助”。"
    )
    elements: list[dict[str, Any]] = [
        {"tag": "markdown", "content": content},
        {"tag": "hr"},
        {"tag": "markdown", "content": "**常用入口**"},
    ]
    actions = _button_actions(
        [
            ("打开入职工作台", detail_url),
            ("提交材料", material_url),
        ]
    )
    if actions:
        elements.append(actions)

    return {
        "config": {"wide_screen_mode": True},
        "elements": elements,
        "header": {"title": {"tag": "plain_text", "content": "入职助手"}, "template": "blue"},
    }


def welcome_text(
    candidate_name: str,
    expected_onboard_date: str,
    *,
    detail_url: str | None = None,
    material_url: str | None = None,
) -> str:
    """Build a plain-text candidate onboarding welcome message."""

    lines = [
        f"{candidate_name}你好，我是你的入职协同助手。你的入职流程已经建好了。",
        f"预计入职日期：{expected_onboard_date}",
        "",
        "接下来我会帮你跟进入职进度、材料提交和审核反馈；关键进展也会同步给 HR 同事。",
        "你可以直接回复“进度”“材料”“帮助”，也可以打开下面入口处理。",
    ]
    if detail_url:
        lines.append(f"入职工作台：{detail_url}")
    if material_url:
        lines.append(f"材料提交：{material_url}")
    return "\n".join(lines)


def onboarding_chat_intro_card(
    candidate_name: str,
    expected_onboard_date: str,
    *,
    detail_url: str | None = None,
) -> dict[str, Any]:
    """Build an onboarding group introduction card."""

    content = (
        f"大家好，我是 {candidate_name} 入职流程的协同助手。\n"
        f"**预计入职日期**：{expected_onboard_date}\n\n"
        "**我会在群里同步这些事项**：\n"
        "- 候选人可以查看进度、提交材料、确认当前节点；\n"
        "- HRBP/负责人可以关注节点进展、超时提醒和材料反馈；\n"
        "- 群内也可以直接发送“进度”“材料”“帮助”。"
    )
    elements: list[dict[str, Any]] = [{"tag": "markdown", "content": content}]
    if detail_url:
        elements.append(_button_action("打开入职工作台", detail_url))
    return {
        "config": {"wide_screen_mode": True},
        "elements": elements,
        "header": {"title": {"tag": "plain_text", "content": "入职协同群已创建"}, "template": "turquoise"},
    }


def progress_card(
    progress: dict[str, Any],
    *,
    detail_url: str | None = None,
    material_url: str | None = None,
    confirm_url: str | None = None,
) -> dict[str, Any]:
    """Build a candidate-facing progress card JSON."""

    nodes = progress.get("nodes", [])[:8]
    content = "\n".join(
        [
            f"**当前阶段**：{progress.get('current_stage', '-')}",
            f"**下一步**：{progress.get('next_step', '-')}",
            f"**截止时间**：{progress.get('next_due_date', '-')}",
        ]
    )
    workspace_url = detail_url or material_url
    actions = _button_actions(
        [
            ("进入入职工作台", workspace_url),
            ("确认当前节点", confirm_url),
        ]
    )
    elements: list[dict[str, Any]] = [
        {"tag": "markdown", "content": content},
        _progress_bar(
            approved_count=int(progress.get("approved_count", 0) or 0),
            total_count=int(progress.get("total_count", 0) or 0),
            overall_status=str(progress.get("overall_status", "-")),
        ),
        {"tag": "hr"},
        {"tag": "markdown", "content": "**近期节点**"},
    ]
    elements.extend(_node_table_elements(nodes))
    if actions:
        elements.append(actions)
    return {
        "config": {"wide_screen_mode": True},
        "elements": elements,
        "header": {"title": {"tag": "plain_text", "content": "入职进度"}, "template": "turquoise"},
    }


def material_request_card(
    required_materials: list[str],
    *,
    candidate_name: str | None = None,
    employment_type: str | None = None,
    detail_url: str | None = None,
    submit_url: str | None = None,
) -> dict[str, Any]:
    """Build a candidate-facing material request card JSON."""

    content_lines = ["请按要求上传清晰彩色扫描件；被驳回材料会在详情页展示修改意见。"]
    if candidate_name:
        content_lines.insert(0, f"**候选人**：{candidate_name}")
    if employment_type:
        content_lines.insert(1 if candidate_name else 0, f"**材料口径**：{employment_type}")
    workspace_url = submit_url or detail_url
    actions = _button_actions([("进入入职工作台", workspace_url)])
    elements: list[dict[str, Any]] = [
        {"tag": "markdown", "content": "\n".join(content_lines)},
        {"tag": "hr"},
        {"tag": "markdown", "content": "**材料清单**"},
    ]
    elements.extend(_material_table_elements(required_materials[:14]))
    if len(required_materials) > 14:
        elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": f"其余 {len(required_materials) - 14} 项请打开详情页查看。"}]})
    if actions:
        elements.append(actions)
    return {
        "config": {"wide_screen_mode": True},
        "elements": elements,
        "header": {"title": {"tag": "plain_text", "content": "材料提交清单"}, "template": "wathet"},
    }


def rejection_card(material_type: str, reason: str) -> dict[str, Any]:
    """Build a material rejection card JSON."""

    return {
        "config": {"wide_screen_mode": True},
        "elements": [{"tag": "markdown", "content": f"{material_type} 未通过审核：{reason}"}],
        "header": {"title": {"tag": "plain_text", "content": "材料需补充"}, "template": "red"},
    }


def overdue_node_card(
    *,
    candidate_name: str,
    node_name: str,
    due_date: str,
    overdue_days: int,
    escalation_level: int,
    action_url: str | None = None,
    audience: str = "owner",
    manager_label: str | None = None,
    guidance: str | None = None,
    action_text: str | None = None,
) -> dict[str, Any]:
    """Build an overdue workflow reminder card for owners or candidates."""

    display_name = NODE_DISPLAY_NAMES.get(node_name, node_name)
    template = "red" if escalation_level >= 2 else "orange"
    manager_line = f"**部门负责人**：{manager_label}\n" if manager_label else ""
    guidance_text = guidance or _node_overdue_guidance(node_name, audience)
    content = (
        f"**候选人**：{candidate_name}\n"
        f"{manager_line}"
        f"**超时节点**：{display_name}\n"
        f"**原截止日期**：{due_date}\n"
        f"**已超时**：{overdue_days} 天\n"
        f"**升级等级**：{'升级提醒' if escalation_level >= 2 else '普通提醒'}\n\n"
        f"{_clip_card_text(guidance_text, 900)}"
    )
    elements: list[dict[str, Any]] = [{"tag": "markdown", "content": content}]
    if action_url:
        button_text = action_text or ("打开入职进度" if audience == "candidate" else "打开 HR 后台")
        elements.append(_button_action(button_text, action_url))
    return {
        "config": {"wide_screen_mode": True},
        "elements": elements,
        "header": {"title": {"tag": "plain_text", "content": "入职节点超时提醒"}, "template": template},
    }


def material_review_result_card(
    *,
    candidate_name: str,
    material_type: str,
    decision: str,
    message_to_candidate: str,
    failed_checks: list[str] | None = None,
    action_url: str | None = None,
) -> dict[str, Any]:
    """Build a candidate-facing material review result card."""

    standard = get_material_standard(material_type)
    material_name = standard.display_name if standard else material_type
    title = _material_review_card_title(decision, message_to_candidate)
    template = "green" if decision == "auto_approved" else "red" if decision == "auto_rejected" else "orange"
    result_label = {
        "auto_approved": "已通过",
        "auto_rejected": "未通过",
        "manual_review": "需人工复核",
    }.get(decision, decision)
    details = "\n".join(f"- {item}" for item in (failed_checks or [])[:6])
    content = (
        f"**候选人**：{candidate_name}\n"
        f"**材料**：{material_name}\n"
        f"**审核结果**：{result_label}\n\n"
        f"{_clip_card_text(message_to_candidate, 700)}"
    )
    if details:
        content += f"\n\n**需要关注**：\n{_clip_card_text(details, 700)}"
    elements: list[dict[str, Any]] = [{"tag": "markdown", "content": content}]
    if action_url:
        elements.append(_button_action("查看入职进度", action_url))
    return {
        "config": {"wide_screen_mode": True},
        "elements": elements,
        "header": {"title": {"tag": "plain_text", "content": title}, "template": template},
    }


def material_review_summary_card(
    *,
    candidate_name: str,
    material_type: str,
    results: list[dict[str, str]],
    action_url: str | None = None,
) -> dict[str, Any]:
    """Build a candidate-facing summary card for a batch material upload."""

    standard = get_material_standard(material_type)
    material_name = standard.display_name if standard else material_type
    approved_count = sum(1 for item in results if _material_summary_file_passed(item))
    manual_count = sum(1 for item in results if item.get("decision") == "manual_review")
    rejected_count = sum(1 for item in results if item.get("decision") == "auto_rejected" and not _is_supplement_material_message(item.get("message", "")))
    missing_items = _material_summary_missing_items(results)
    template = "red" if rejected_count else "orange" if manual_count else "green"
    summary = (
        f"**候选人**：{candidate_name}\n"
        f"**材料**：{material_name}\n"
        f"**本次提交**：{len(results)} 个文件\n"
        f"**汇总**：文件已通过 {approved_count} 个；仍需补充 {len(missing_items)} 项；未通过 {rejected_count} 个；人工复核 {manual_count} 个"
    )
    details = "\n".join(_material_summary_line(item) for item in results[:8])
    content = f"{summary}\n\n**文件结果**：\n{_clip_card_text(details, 900)}"
    if missing_items:
        content += "\n\n**仍需补充**：\n" + _clip_card_text("\n".join(f"- {item}" for item in missing_items[:8]), 500)
    if len(results) > 8:
        content += f"\n- 其余 {len(results) - 8} 个文件请打开入职工作台查看。"
    elements: list[dict[str, Any]] = [{"tag": "markdown", "content": content}]
    if action_url:
        elements.append(_button_action("查看入职进度", action_url))
    return {
        "config": {"wide_screen_mode": True},
        "elements": elements,
        "header": {"title": {"tag": "plain_text", "content": "材料批量审核结果"}, "template": template},
    }


def material_review_overall_summary_card(
    *,
    candidate_name: str,
    groups: list[dict[str, Any]],
    action_url: str | None = None,
) -> dict[str, Any]:
    """Build a candidate-facing summary card for a multi-material submission."""

    total_files = sum(len(group.get("results") or []) for group in groups)
    all_results = [item for group in groups for item in group.get("results", [])]
    approved_count = sum(1 for item in all_results if _material_summary_file_passed(item))
    manual_count = sum(1 for item in all_results if item.get("decision") == "manual_review")
    rejected_count = sum(
        1
        for item in all_results
        if item.get("decision") == "auto_rejected" and not _is_supplement_material_message(item.get("message", ""))
    )
    missing_items = _overall_summary_missing_items(groups)
    template = "red" if rejected_count else "orange" if manual_count or missing_items else "green"
    summary = (
        f"**候选人**：{candidate_name}\n"
        f"**本次提交**：{len(groups)} 个材料项，{total_files} 个文件\n"
        f"**汇总**：已提交 {total_files} 个；文件已通过 {approved_count} 个；"
        f"仍需补充 {len(missing_items)} 项；已驳回 {rejected_count} 个；需复核 {manual_count} 个"
    )
    material_lines = "\n".join(_overall_material_line(group) for group in groups[:8])
    file_lines = "\n".join(_overall_file_line(group, item) for group in groups for item in (group.get("results") or [])[:4])
    content = f"{summary}\n\n**材料项结果**：\n{_clip_card_text(material_lines, 900)}"
    if file_lines:
        content += f"\n\n**文件结果**：\n{_clip_card_text(file_lines, 900)}"
    if missing_items:
        content += "\n\n**仍需补充**：\n" + _clip_card_text("\n".join(f"- {item}" for item in missing_items[:8]), 500)
    if len(groups) > 8:
        content += f"\n- 其余 {len(groups) - 8} 个材料项请打开入职工作台查看。"
    elements: list[dict[str, Any]] = [{"tag": "markdown", "content": content}]
    if action_url:
        elements.append(_button_action("查看入职进度", action_url))
    return {
        "config": {"wide_screen_mode": True},
        "elements": elements,
        "header": {"title": {"tag": "plain_text", "content": "材料统一提交结果"}, "template": template},
    }


def _material_summary_line(item: dict[str, str]) -> str:
    file_name = item.get("file_name") or "未命名文件"
    decision = item.get("decision") or ""
    message = item.get("message") or ""
    missing_items = _supplement_material_items(message)
    label = {
        "auto_approved": "已通过",
        "auto_rejected": "文件已通过" if missing_items else "未通过",
        "manual_review": "人工复核",
    }.get(decision, decision or "已提交")
    if missing_items:
        reason = f"；仍需补充：{_clip_card_text('、'.join(missing_items), 80)}"
    else:
        reason = f"：{_clip_card_text(message, 90)}" if message else ""
    return f"- {file_name}：{label}{reason}"


def _material_summary_file_passed(item: dict[str, str]) -> bool:
    decision = item.get("decision")
    message = item.get("message") or ""
    return decision == "auto_approved" or (decision == "auto_rejected" and _is_supplement_material_message(message))


def _material_summary_missing_items(results: list[dict[str, str]]) -> list[str]:
    missing: list[str] = []
    for item in results:
        for value in _supplement_material_items(item.get("message") or ""):
            if value not in missing:
                missing.append(value)
    return missing


def _overall_material_line(group: dict[str, Any]) -> str:
    material_name = group.get("material_name") or group.get("material_type") or "材料"
    results = group.get("results") or []
    approved_count = sum(1 for item in results if _material_summary_file_passed(item))
    manual_count = sum(1 for item in results if item.get("decision") == "manual_review")
    rejected_count = sum(
        1
        for item in results
        if item.get("decision") == "auto_rejected" and not _is_supplement_material_message(item.get("message", ""))
    )
    missing_count = len(_material_summary_missing_items(results))
    return (
        f"- {material_name}：提交 {len(results)} 个；文件已通过 {approved_count} 个；"
        f"仍需补充 {missing_count} 项；已驳回 {rejected_count} 个；需复核 {manual_count} 个"
    )


def _overall_file_line(group: dict[str, Any], item: dict[str, str]) -> str:
    material_name = group.get("material_name") or group.get("material_type") or "材料"
    return _material_summary_line({**item, "file_name": f"{material_name} / {item.get('file_name') or '未命名文件'}"})


def _overall_summary_missing_items(groups: list[dict[str, Any]]) -> list[str]:
    missing: list[str] = []
    for group in groups:
        material_name = group.get("material_name") or group.get("material_type") or "材料"
        for value in _material_summary_missing_items(group.get("results") or []):
            item = f"{material_name}：{value}"
            if item not in missing:
                missing.append(item)
    return missing


def _supplement_material_items(message: str) -> list[str]:
    text = str(message or "")
    items: list[str] = []
    match = re.search(r"还缺[:：]\s*([^。；;]+)", text)
    if match:
        items.extend(part.strip() for part in re.split(r"[、,，]", match.group(1)) if part.strip())
    if not items and "请先上传求职简历" in text:
        items.append("求职简历")
    return items


def _material_review_card_title(decision: str, message_to_candidate: str) -> str:
    if decision == "auto_approved":
        return "材料审核通过"
    if decision == "auto_rejected" and not _is_supplement_material_message(message_to_candidate):
        return "材料未通过"
    return "材料需补充"


def _is_supplement_material_message(message: str) -> bool:
    return any(keyword in (message or "") for keyword in ("还缺", "继续上传缺少", "请继续上传", "请先上传求职简历"))


def qa_answer_card(
    *,
    question: str,
    answer: str,
    sources: list[dict[str, str]] | None = None,
    used_external_search: bool = False,
    needs_handoff: bool = False,
) -> dict[str, Any]:
    """Build a consistent Feishu card for HR assistant QA replies."""

    template = "orange" if needs_handoff else "blue"
    display_sources = sources or (
        [
            {
                "id": "G1",
                "title": "外部联网检索",
                "path": "模型已使用外部联网检索生成答案，但当前响应未返回可解析来源链接。",
            }
        ]
        if used_external_search
        else []
    )
    elements = _qa_answer_elements(question, answer)
    if display_sources:
        source_lines = []
        for source in display_sources[:4]:
            source_id = source.get("id", "-")
            title = source.get("title", "参考资料")
            path = source.get("path", "")
            source_lines.append(
                f"- [{_clip_card_text(source_id, 16)}] {_clip_card_text(title, 80)}"
                + (f"：{_clip_card_text(path, 120)}" if path else "")
            )
        elements.extend(
            [
                {"tag": "hr"},
                {"tag": "markdown", "content": "**参考来源**\n" + "\n".join(source_lines)},
            ]
        )
    note = (
        "该问题使用了外部检索，落地执行前建议与 HRBP/SSC 或最新官方口径 double check。"
        if used_external_search
        else "政策和流程可能更新，落地执行前建议与 HRBP/SSC 或最新官方口径 double check。"
    )
    if display_sources or used_external_search:
        elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": note}]})
    return {
        "config": {"wide_screen_mode": True},
        "elements": elements,
        "header": {"title": {"tag": "plain_text", "content": "智能问答"}, "template": template},
    }


def _clip_card_text(text: str, limit: int) -> str:
    normalized = text.strip()
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit].rstrip()}..."


def _qa_answer_elements(question: str, answer: str) -> list[dict[str, Any]]:
    segments = _split_markdown_table_segments(answer)
    elements: list[dict[str, Any]] = []
    question_line = f"**问题**：{_clip_card_text(question, 160)}"

    if not segments:
        return [{"tag": "markdown", "content": question_line}]

    first_markdown = True
    for kind, payload in segments:
        if kind == "markdown":
            content = str(payload).strip()
            if not content and not first_markdown:
                continue
            prefix = f"{question_line}\n\n" if first_markdown else ""
            for index, part in enumerate(_split_card_text(content, 1400, max_parts=4)):
                if part or prefix:
                    elements.append({"tag": "markdown", "content": f"{prefix if index == 0 else ''}{part}".strip()})
            first_markdown = False
            continue

        if first_markdown:
            elements.append({"tag": "markdown", "content": question_line})
            first_markdown = False
        elements.append(payload)

    return elements or [{"tag": "markdown", "content": question_line}]


def _split_markdown_table_segments(text: str) -> list[tuple[str, str | dict[str, Any]]]:
    lines = text.strip().splitlines()
    segments: list[tuple[str, str | dict[str, Any]]] = []
    markdown_buffer: list[str] = []
    index = 0

    while index < len(lines):
        if index + 1 < len(lines) and _is_table_row(lines[index]) and _is_table_separator(lines[index + 1]):
            table_lines = [lines[index], lines[index + 1]]
            index += 2
            while index < len(lines) and _is_table_row(lines[index]):
                table_lines.append(lines[index])
                index += 1

            table = _markdown_table_to_card(table_lines)
            if table:
                if markdown_buffer:
                    segments.append(("markdown", "\n".join(markdown_buffer).strip()))
                    markdown_buffer = []
                segments.append(("table", table))
            else:
                markdown_buffer.extend(table_lines)
            continue

        markdown_buffer.append(lines[index])
        index += 1

    if markdown_buffer:
        segments.append(("markdown", "\n".join(markdown_buffer).strip()))
    return segments


def _is_table_row(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2


def _is_table_separator(line: str) -> bool:
    if not _is_table_row(line):
        return False
    cells = _split_table_row(line)
    return bool(cells) and all(set(cell.replace(":", "").strip()) <= {"-"} and "-" in cell for cell in cells)


def _split_table_row(line: str) -> list[str]:
    stripped = line.strip().strip("|")
    return [cell.strip() for cell in stripped.split("|")]


def _markdown_table_to_card(lines: list[str]) -> dict[str, Any] | None:
    if len(lines) < 3:
        return None
    headers = [_plain_table_cell(cell) for cell in _split_table_row(lines[0])]
    body_rows = [[_plain_table_cell(cell) for cell in _split_table_row(line)] for line in lines[2:]]
    if not headers or not body_rows:
        return None
    if any(len(row) != len(headers) for row in body_rows):
        return None

    keys = [f"col_{index}" for index, _ in enumerate(headers)]
    return {
        "tag": "table",
        "page_size": min(max(len(body_rows), 1), 10),
        "row_height": "low",
        "freeze_first_column": True,
        "header_style": {
            "text_align": "left",
            "text_size": "normal",
            "background_style": "none",
            "text_color": "grey",
            "bold": True,
            "lines": 1,
        },
        "columns": [
            {
                "name": key,
                "display_name": _clip_card_text(header or "-", 24),
                "data_type": "text",
                "horizontal_align": "left",
                "vertical_align": "top",
            }
            for key, header in zip(keys, headers)
        ],
        "rows": [
            {key: _clip_card_text(value or "-", 120) for key, value in zip(keys, row)}
            for row in body_rows[:10]
        ],
    }


def _plain_table_cell(cell: str) -> str:
    return cell.replace("**", "").replace("`", "").strip()


def _split_card_text(text: str, limit: int, *, max_parts: int) -> list[str]:
    normalized = text.strip()
    if not normalized:
        return [""]
    parts: list[str] = []
    remaining = normalized
    while remaining and len(parts) < max_parts:
        if len(remaining) <= limit:
            parts.append(remaining)
            break
        split_at = max(
            remaining.rfind("\n\n", 0, limit),
            remaining.rfind("\n", 0, limit),
            remaining.rfind("。", 0, limit),
            remaining.rfind("；", 0, limit),
        )
        if split_at < limit // 2:
            split_at = limit
        part = remaining[:split_at].strip()
        if part:
            parts.append(part)
        remaining = remaining[split_at:].strip()
    if remaining and parts:
        parts[-1] = f"{parts[-1].rstrip()}\n\n（后续内容较长，已收起；可继续追问具体细节。）"
    return parts


def _progress_bar(*, approved_count: int, total_count: int, overall_status: str) -> dict[str, Any]:
    total = max(total_count, 1)
    percent = round(approved_count / total * 100)
    filled = min(10, max(0, round(percent / 10)))
    bar = "■" * filled + "□" * (10 - filled)
    return {
        "tag": "markdown",
        "content": f"**完成进度**：{approved_count}/{total_count}  `{bar}`  {percent}%\n**整体状态**：{overall_status}",
    }


def _node_table_elements(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not nodes:
        return [{"tag": "markdown", "content": "暂无节点。"}]
    elements: list[dict[str, Any]] = [
        {"tag": "column_set", "flex_mode": "none", "background_style": "grey", "columns": [
            _column("节点", "weighted", 2),
            _column("状态", "weighted", 1),
            _column("截止", "weighted", 2),
        ]}
    ]
    for node in nodes:
        status = str(node.get("status", "-"))
        elements.append(
            {
                "tag": "column_set",
                "flex_mode": "none",
                "columns": [
                    _column(f"{_status_icon(status)} {node.get('node_name', '-')}", "weighted", 2),
                    _column(status, "weighted", 1),
                    _column(str(node.get("due_date", "-")), "weighted", 2),
                ],
            }
        )
    return elements


def _material_table_elements(materials: list[str]) -> list[dict[str, Any]]:
    if not materials:
        return [{"tag": "markdown", "content": "暂无材料清单。"}]
    elements: list[dict[str, Any]] = [
        {"tag": "column_set", "flex_mode": "none", "background_style": "grey", "columns": [
            _column("材料", "weighted", 3),
            _column("状态", "weighted", 1),
        ]}
    ]
    for item in materials:
        name, status = _split_material_line(item)
        elements.append(
            {
                "tag": "column_set",
                "flex_mode": "none",
                "columns": [
                    _column(name, "weighted", 3),
                    _column(f"{_status_icon(status)} {status}", "weighted", 1),
                ],
            }
        )
    return elements


def _column(content: str, width: str, weight: int) -> dict[str, Any]:
    return {
        "tag": "column",
        "width": width,
        "weight": weight,
        "elements": [{"tag": "markdown", "content": _clip_card_text(content, 80)}],
    }


def _split_material_line(item: str) -> tuple[str, str]:
    if "：" in item:
        name, status = item.rsplit("：", 1)
        return name, status
    if ":" in item:
        name, status = item.rsplit(":", 1)
        return name, status
    return item, "-"


def _status_icon(status: str) -> str:
    if status in {"已完成", "已通过", "自动通过"}:
        return "✅"
    if status in {"已超时", "已驳回", "自动驳回", "有风险"}:
        return "⚠️"
    if status in {"进行中", "已提交", "已确认", "人工复核"}:
        return "🔵"
    return "⬜"


def _node_overdue_guidance(node_name: str, audience: str) -> str:
    if audience == "hr_probation_email" and node_name in {"probation_month_3", "probation_month_4"}:
        return "请给该候选人的部门负责人发送试用期考察邮件，并在 HR 后台记录发送或反馈状态。"

    candidate_guidance = {
        "background_check": "请按 HR 通知配合背调信息确认和授权材料提交；如已经完成，请在入职进度页点击“我已确认”。",
        "medical_check": "请尽快完成入职体检，并保留完整体检报告；如已经完成，请在入职进度页点击“我已确认”。",
        "document_submission": "请进入材料提交页，按清单上传预入职材料；如已经提交，请在入职进度页点击“我已确认”。",
        "entry_preparation": "请按入职通知完成报到准备，到岗后配合领取电脑、工牌和办公权限；如已经完成，请在入职进度页点击“我已确认”。",
        "contract_signing": "请按 HR 或电子签平台提示完成劳动合同签署；如已经签署，请在入职进度页点击“我已确认”。",
        "compliance_commitment": "请在 OA 或 HR 通知入口完成合规承诺相关待办；如已经完成，请在入职进度页点击“我已确认”。",
        "conflict_of_interest_declaration": "请在人员规范系统完成人事及投资申报；如已经完成，请在入职进度页点击“我已确认”。",
        "role_qualification_registration": "如已通过岗位资格考试，请按 HR 通知提交注册所需材料；如已经提交，请在入职进度页点击“我已确认”。",
        "records_transfer": "党员、预备党员或团员请按指引办理党团组织关系转接；如已经提交材料，请在入职进度页点击“我已确认”。",
        "probation_month_3": "请配合直属经理完成试用期阶段沟通和材料准备；如已经完成，请在入职进度页点击“我已确认”。",
        "probation_month_4": "请配合直属经理完成试用期阶段沟通和材料准备；如已经完成，请在入职进度页点击“我已确认”。",
        "probation_month_5": "请提前准备转正申请和试用期总结材料；如已经完成，请在入职进度页点击“我已确认”。",
        "conversion_review": "请按 HR 或直属经理通知完成转正考核流程；如已经完成，请在入职进度页点击“我已确认”。",
    }
    if audience == "candidate":
        return candidate_guidance.get(node_name, "请按 HR 通知尽快完成该节点；如已经完成，请在入职进度页点击“我已确认”。")
    return (
        candidate_guidance.get(node_name, "请确认候选人是否已完成该节点，并同步处理记录。")
        + " 若候选人已完成，请在 HR 后台更新节点状态。"
    )


def overdue_node_guidance(node_name: str, audience: str) -> str:
    """Return the deterministic overdue reminder guidance used when AI copy is unavailable."""

    return _node_overdue_guidance(node_name, audience)


def _button_action(text: str, url: str) -> dict[str, Any]:
    action = _button_actions([(text, url)])
    return action or {"tag": "action", "actions": []}


def _button_actions(buttons: list[tuple[str, str | None]]) -> dict[str, Any] | None:
    actions = []
    for text, url in buttons:
        if not url:
            continue
        actions.append(
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": text},
                "type": "primary",
                "url": url,
            }
        )
    if not actions:
        return None
    return {
        "tag": "action",
        "actions": actions,
    }
