from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Any, Optional

from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.core.config import get_settings


_basic = HTTPBasic(auto_error=False)
CANDIDATE_COOKIE = "candidate_access"


def require_hr_access(
    request: Request,
    credentials: Optional[HTTPBasicCredentials] = Depends(_basic),
) -> None:
    """Require HTTP Basic auth for HR/admin surfaces outside demo mode."""

    settings = get_settings()
    if settings.demo_mode or settings.environment == "test":
        return
    if not settings.hr_admin_password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="HR authentication is not configured",
        )
    username_ok = bool(credentials) and secrets.compare_digest(
        credentials.username.encode("utf-8"), settings.hr_admin_username.encode("utf-8")
    )
    password_ok = bool(credentials) and secrets.compare_digest(
        credentials.password.encode("utf-8"), settings.hr_admin_password.encode("utf-8")
    )
    if not (username_ok and password_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid HR credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


def issue_candidate_token(case_id: int, *, expires_at: int | None = None) -> str:
    """Issue a time-limited token bound to one candidate case."""

    settings = get_settings()
    if settings.demo_mode:
        return "demo"
    if not settings.app_session_secret:
        raise RuntimeError("APP_SESSION_SECRET is required for candidate links")
    expiry = expires_at or int(time.time()) + settings.candidate_token_ttl_seconds
    payload = f"{case_id}:{expiry}"
    signature = hmac.new(
        settings.app_session_secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{expiry}.{signature}"


def verify_candidate_token(case_id: int, token: str | None, *, now: int | None = None) -> bool:
    """Verify expiry and HMAC without disclosing whether a case exists."""

    settings = get_settings()
    if settings.demo_mode or settings.environment == "test":
        return True
    if not token or not settings.app_session_secret:
        return False
    expiry_text, separator, signature = token.partition(".")
    if not separator or not expiry_text.isdigit():
        return False
    expiry = int(expiry_text)
    if expiry < (now or int(time.time())):
        return False
    payload = f"{case_id}:{expiry}"
    expected = hmac.new(
        settings.app_session_secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


def require_candidate_access(request: Request, case_id: int) -> str | None:
    """Authorize a candidate request using a query token or bound cookie."""

    query_token = request.query_params.get("access_token")
    cookie_token = request.cookies.get(CANDIDATE_COOKIE)
    token = query_token or cookie_token
    if not verify_candidate_token(case_id, token):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="candidate workspace not found")
    return query_token


def attach_candidate_cookie(response: Response, case_id: int, query_token: str | None) -> None:
    """Persist a verified link token in a restricted browser cookie."""

    settings = get_settings()
    if settings.demo_mode or settings.environment == "test" or not query_token:
        return
    response.set_cookie(
        key=CANDIDATE_COOKIE,
        value=query_token,
        max_age=settings.candidate_token_ttl_seconds,
        httponly=True,
        secure=settings.environment == "prod",
        samesite="strict",
        path=f"/ui/candidate/{case_id}",
    )


def require_same_origin(request: Request) -> None:
    """Reject cross-site browser state changes while allowing non-browser clients."""

    origin = request.headers.get("origin")
    if not origin:
        return
    expected = f"{request.url.scheme}://{request.headers.get('host', '')}".rstrip("/")
    if not secrets.compare_digest(origin.rstrip("/"), expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="cross-site request rejected")


def verify_feishu_token(token: str | None) -> bool:
    """Validate the Feishu verification token and fail closed when absent."""

    settings = get_settings()
    expected = settings.feishu_verification_token
    if not expected:
        return settings.demo_mode or settings.environment == "test"
    return bool(token) and hmac.compare_digest(str(token), expected)


def verify_feishu_event_freshness(header: dict[str, Any], *, now: int | None = None) -> bool:
    """Enforce a bounded callback age when Feishu supplies a creation time."""

    settings = get_settings()
    if settings.demo_mode or settings.environment == "test":
        return True
    raw = header.get("create_time")
    if raw in (None, ""):
        return False
    try:
        created = int(str(raw))
    except ValueError:
        return False
    if created > 10_000_000_000:
        created //= 1000
    age = abs((now or int(time.time())) - created)
    return age <= settings.feishu_max_event_age_seconds


def decrypt_feishu_payload_if_needed(payload: dict[str, Any]) -> dict[str, Any]:
    """Reject encrypted callbacks until authenticated decryption is implemented."""

    if payload.get("encrypt"):
        raise ValueError("encrypted Feishu callbacks are not supported; use long connection or add verified decryption")
    return payload
