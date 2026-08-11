from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class LLMRequest:
    system_prompt: str
    user_prompt: str
    json_schema: dict[str, Any] | None = None


@dataclass(frozen=True)
class LLMResult:
    text: str
    provider: str
    model: str


@dataclass(frozen=True)
class OCRRequest:
    file_name: str
    content: bytes
    mime_type: str
    material_type: str


@dataclass(frozen=True)
class OCRResult:
    ocr_text: str
    extracted_fields: dict[str, Any] = field(default_factory=dict)
    quality_notes: tuple[str, ...] = ()
    provider: str = "unknown"


class LLMProvider(Protocol):
    async def generate(self, request: LLMRequest) -> LLMResult: ...


class OCRProvider(Protocol):
    async def extract(self, request: OCRRequest) -> OCRResult: ...
