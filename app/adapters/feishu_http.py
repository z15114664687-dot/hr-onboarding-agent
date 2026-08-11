from __future__ import annotations

import asyncio
from typing import Any

import httpx

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


async def request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    max_attempts: int = 3,
    **kwargs: Any,
) -> httpx.Response:
    """Send a Feishu HTTP request with small retries for transient failures."""

    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = await client.request(method, url, **kwargs)
            if response.status_code not in RETRYABLE_STATUS_CODES or attempt == max_attempts:
                return response
        except (httpx.ConnectError, httpx.ProxyError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
            last_error = exc
            if attempt == max_attempts:
                raise
        await asyncio.sleep(0.5 * attempt)
    if last_error:
        raise last_error
    raise RuntimeError("Feishu request retry loop exited unexpectedly")
