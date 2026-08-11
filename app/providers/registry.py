from __future__ import annotations

from app.core.config import get_settings
from app.providers.base import LLMProvider, OCRProvider
from app.providers.legacy import ArkOCRProvider, GeminiOCRProvider
from app.providers.mock import MockLLMProvider, MockOCRProvider
from app.providers.openai import OpenAIProvider


def get_llm_provider(name: str | None = None) -> LLMProvider:
    provider = (name or get_settings().llm_provider).lower()
    if provider == "mock":
        return MockLLMProvider()
    if provider == "openai":
        return OpenAIProvider()
    raise ValueError(f"{provider} uses the existing QA adapter; see docs/LLM_PROVIDERS.md")


def get_ocr_provider(name: str | None = None) -> OCRProvider:
    provider = (name or get_settings().ocr_provider).lower()
    if provider == "mock":
        return MockOCRProvider()
    if provider == "openai":
        return OpenAIProvider()
    if provider == "gemini":
        return GeminiOCRProvider()
    if provider == "ark":
        return ArkOCRProvider()
    raise ValueError(f"unsupported OCR provider: {provider}")
