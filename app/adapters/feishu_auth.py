from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from app.adapters.feishu_http import request_with_retry
from app.core.config import get_settings

FEISHU_API_BASE = "https://open.feishu.cn"


@dataclass
class TokenCache:
    access_token: str | None = None
    expires_at: float = 0


_token_cache = TokenCache()


async def get_tenant_access_token() -> str:
    """Return cached Feishu tenant_access_token, refreshing before expiry."""

    now = time.time()
    if _token_cache.access_token and _token_cache.expires_at - 120 > now:
        return _token_cache.access_token

    settings = get_settings()
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        raise RuntimeError("FEISHU_APP_ID and FEISHU_APP_SECRET are required for Feishu API calls")

    async with httpx.AsyncClient(base_url=FEISHU_API_BASE, timeout=10) as client:
        response = await request_with_retry(
            client,
            "POST",
            "/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": settings.feishu_app_id, "app_secret": settings.feishu_app_secret},
        )
        response.raise_for_status()
        data = response.json()
    if data.get("code") != 0:
        raise RuntimeError(f"failed to get Feishu tenant_access_token: code={data.get('code')} msg={data.get('msg')}")

    _token_cache.access_token = data["tenant_access_token"]
    _token_cache.expires_at = now + int(data.get("expire", 7200))
    return _token_cache.access_token
