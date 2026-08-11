import os

import pytest

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DEMO_MODE", "true")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/hr-onboarding-agent-pytest.db")

from app.core.config import get_settings


@pytest.fixture(autouse=True)
def isolate_external_model_keys(monkeypatch):
    """Keep tests from accidentally calling live model APIs from local .env."""

    for name in (
        "ZHIPU_API_KEY",
        "ARK_API_KEY",
        "GEMINI_API_KEY",
        "GEMINI_GATEWAY_URL",
        "GEMINI_GATEWAY_TOKEN",
        "DEEPSEEK_API_KEY",
    ):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("ALLOW_EXTERNAL_AI", "true")
    monkeypatch.setenv("OCR_PROVIDER", "ark")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
