from __future__ import annotations

import logging
from typing import Any, Literal

import httpx

from app.services.onboarding_workflow import NODE_DISPLAY_NAMES

logger = logging.getLogger(__name__)

ReminderAudience = Literal["candidate", "owner", "hr_probation_email"]


async def generate_overdue_guidance(
    *,
    candidate_name: str,
    node_name: str,
    due_date: str,
    overdue_days: int,
    audience: ReminderAudience,
    fallback: str,
    manager_label: str | None = None,
) -> str:
    """Return stable overdue reminder copy."""

    logger.info("reminder_guidance_generation_skipped node_name=%s audience=%s reason=stable_template", node_name, audience)
    return fallback


def _build_prompt(
    *,
    candidate_name: str,
    node_name: str,
    due_date: str,
    overdue_days: int,
    audience: ReminderAudience,
    fallback: str,
    manager_label: str | None,
) -> str:
    node_label = NODE_DISPLAY_NAMES.get(node_name, node_name)
    if audience == "candidate":
        audience_rule = (
            "收件人是候选人。请写清楚候选人现在应该去哪里处理、完成后怎么在入职进度页确认。"
            "不要出现 HR 后台、多维表格、节点负责人等内部系统话术。"
        )
    elif audience == "hr_probation_email":
        audience_rule = (
            "收件人是 HR 或节点负责人。请提醒其给候选人的部门负责人发送试用期考察邮件，"
            "并在 HR 后台记录发送或反馈状态。"
        )
    else:
        audience_rule = (
            "收件人是 HR 或节点负责人。请写成催办和处理建议，说明要联系候选人、核对完成情况，"
            "如果已完成则在 HR 后台更新节点状态。"
        )
    manager_line = f"部门负责人信息：{manager_label}\n" if manager_label else ""
    return (
        "你是公司 HR 入职助理，只输出一段中文提醒文案，80 到 150 字，不要标题、不要项目符号。\n"
        "只能依据固定兜底口径和已给出的节点信息改写，不要编造公司官网、个人中心、OA、邮箱、系统名或未给出的提交入口。\n"
        f"{audience_rule}\n"
        f"候选人：{candidate_name}\n"
        f"超时节点：{node_label}\n"
        f"原截止日期：{due_date}\n"
        f"已超时：{overdue_days} 天\n"
        f"{manager_line}"
        f"固定兜底口径：{fallback}"
    )


async def _call_zhipu_reminder_copy(
    *,
    model: str,
    api_key: str,
    prompt: str,
    base_url: str,
    timeout_seconds: float,
) -> str:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你只负责改写 HR 入职流程提醒文案，必须简短、明确、可执行。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 220,
    }
    timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 5.0))
    url = f"{base_url.rstrip('/')}/chat/completions"
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
        response.raise_for_status()
    data = response.json()
    return str((((data.get("choices") or [{}])[0].get("message") or {}).get("content")) or "").strip()


def _clean_guidance(text: str) -> str:
    cleaned = " ".join(str(text or "").strip().split())
    cleaned = cleaned.strip("`*_ ")
    if len(cleaned) > 220:
        cleaned = cleaned[:220].rstrip() + "..."
    return cleaned
