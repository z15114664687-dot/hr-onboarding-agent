from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class FeishuEventEnvelope(BaseModel):
    schema_: Optional[str] = None
    header: Optional[dict[str, Any]] = None
    event: Optional[dict[str, Any]] = None
    challenge: Optional[str] = None
    token: Optional[str] = None
    type: Optional[str] = None


class FeishuChallengeResponse(BaseModel):
    challenge: str
