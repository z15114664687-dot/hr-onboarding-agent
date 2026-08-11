from __future__ import annotations

from typing import Any

import httpx

from app.adapters.feishu_auth import FEISHU_API_BASE, get_tenant_access_token
from app.adapters.feishu_http import request_with_retry


class FeishuChatError(RuntimeError):
    """Raised when Feishu rejects a chat operation."""


async def create_chat(
    *,
    name: str,
    owner_open_id: str | None = None,
    user_open_ids: list[str] | None = None,
    bot_app_ids: list[str] | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Create a Feishu group chat and invite initial users/bots."""

    payload: dict[str, Any] = {"name": name, "chat_mode": "group"}
    if description:
        payload["description"] = description
    if owner_open_id:
        payload["owner_id"] = owner_open_id
    if user_open_ids:
        payload["user_id_list"] = _unique(user_open_ids)
    if bot_app_ids:
        payload["bot_id_list"] = _unique(bot_app_ids)

    return await _chat_request(
        "POST",
        "/open-apis/im/v1/chats",
        params={"user_id_type": "open_id"},
        json=payload,
    )


async def add_chat_members(
    chat_id: str,
    *,
    user_open_ids: list[str] | None = None,
    bot_app_ids: list[str] | None = None,
    succeed_type: int = 1,
) -> dict[str, Any]:
    """Invite users and bots to an existing Feishu group chat."""

    results: dict[str, Any] = {"users": None, "bots": None}
    users = _unique(user_open_ids or [])
    bots = _unique(bot_app_ids or [])
    if users:
        results["users"] = await _chat_request(
            "POST",
            f"/open-apis/im/v1/chats/{chat_id}/members",
            params={"member_id_type": "open_id", "succeed_type": str(succeed_type)},
            json={"id_list": users},
        )
    if bots:
        results["bots"] = await _chat_request(
            "POST",
            f"/open-apis/im/v1/chats/{chat_id}/members",
            params={"member_id_type": "app_id", "succeed_type": str(succeed_type)},
            json={"id_list": bots},
        )
    return results


async def create_onboarding_chat(
    *,
    name: str,
    owner_open_id: str | None,
    user_open_ids: list[str],
    include_bot: bool = True,
    description: str | None = None,
) -> dict[str, Any]:
    """Create an onboarding group chat; the calling bot joins automatically on Feishu."""

    # The bot that calls create-chat joins automatically; bot_id_list is only for inviting other bots.
    bot_ids: list[str] = []
    return await create_chat(
        name=name,
        owner_open_id=owner_open_id,
        user_open_ids=user_open_ids,
        bot_app_ids=bot_ids,
        description=description,
    )


async def _chat_request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    token = await get_tenant_access_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}
    async with httpx.AsyncClient(base_url=FEISHU_API_BASE, timeout=15) as client:
        response = await request_with_retry(client, method, path, headers=headers, **kwargs)
        status_code = getattr(response, "status_code", 200)
        if status_code >= 400:
            raise FeishuChatError(f"Feishu chat HTTP error: status={status_code}")
        response.raise_for_status()
    data = response.json()
    if data.get("code") != 0:
        raise FeishuChatError(f"Feishu chat operation failed: code={data.get('code')}")
    return data


def extract_chat_id(response: dict[str, Any]) -> str:
    """Extract chat_id from Feishu create-chat response variants."""

    data = response.get("data") if isinstance(response, dict) else None
    if not isinstance(data, dict):
        return ""
    value = data.get("chat_id") or (data.get("chat") or {}).get("chat_id")
    return str(value or "").strip()


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result
