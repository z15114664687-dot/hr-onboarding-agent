from __future__ import annotations

from typing import Any

import httpx

from app.adapters.feishu_auth import FEISHU_API_BASE, get_tenant_access_token
from app.adapters.feishu_http import request_with_retry


async def batch_get_open_ids(*, emails: list[str] | None = None, mobiles: list[str] | None = None) -> dict[str, str]:
    """Resolve Feishu open_id values from same-tenant emails or mobile numbers."""

    email_values = _unique(emails or [])
    mobile_values = _unique(mobiles or [])
    if not email_values and not mobile_values:
        return {}

    token = await get_tenant_access_token()
    payload: dict[str, Any] = {}
    if email_values:
        payload["emails"] = email_values
    if mobile_values:
        payload["mobiles"] = mobile_values

    async with httpx.AsyncClient(base_url=FEISHU_API_BASE, timeout=15) as client:
        response = await request_with_retry(
            client,
            "POST",
            "/open-apis/contact/v3/users/batch_get_id",
            params={"user_id_type": "open_id"},
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
        response.raise_for_status()
    data = response.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Feishu user id lookup failed: code={data.get('code')} msg={data.get('msg')}")
    return _extract_lookup_map(data.get("data") or {})


def _extract_lookup_map(data: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for user in _iter_user_items(data):
        open_id = str(user.get("user_id") or user.get("open_id") or user.get("id") or "").strip()
        if not open_id:
            continue
        for key in ("email", "mobile"):
            value = str(user.get(key) or "").strip()
            if value:
                result[value.lower()] = open_id
    return result


def _iter_user_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for key in ("user_list", "users", "items", "email_users", "mobile_users"):
        value = data.get(key)
        if isinstance(value, list):
            items.extend(item for item in value if isinstance(item, dict))
    return items


def _unique(values: list[str]) -> list[str]:
    unique_values: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in unique_values:
            unique_values.append(text)
    return unique_values
