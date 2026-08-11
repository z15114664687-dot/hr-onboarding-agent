import json

import httpx
import pytest

from app.core.config import get_settings
from app.providers.base import LLMRequest, OCRRequest
from app.providers.openai import OpenAIProvider, _output_text, _post_responses


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _enable_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOW_EXTERNAL_AI", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-key")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-test")


@pytest.mark.asyncio
async def test_openai_provider_requires_explicit_external_ai_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOW_EXTERNAL_AI", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-key")

    with pytest.raises(RuntimeError, match="disabled"):
        await OpenAIProvider().generate(LLMRequest(system_prompt="System", user_prompt="User"))


@pytest.mark.asyncio
async def test_openai_provider_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOW_EXTERNAL_AI", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "")

    with pytest.raises(RuntimeError, match="not configured"):
        await OpenAIProvider().generate(LLMRequest(system_prompt="System", user_prompt="User"))


@pytest.mark.asyncio
async def test_openai_generate_uses_responses_structured_output(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.providers.openai as openai_provider

    _enable_openai(monkeypatch)
    captured: dict[str, object] = {}
    schema = {
        "type": "object",
        "properties": {"decision": {"type": "string"}},
        "required": ["decision"],
        "additionalProperties": False,
    }

    async def fake_post(payload):
        captured.update(payload)
        return {"output_text": '{"decision":"manual_review"}'}

    monkeypatch.setattr(openai_provider, "_post_responses", fake_post)

    result = await OpenAIProvider().generate(
        LLMRequest(system_prompt="System policy", user_prompt="Untrusted evidence", json_schema=schema)
    )

    assert result.text == '{"decision":"manual_review"}'
    assert captured["input"] == [
        {"role": "system", "content": "System policy"},
        {"role": "user", "content": "Untrusted evidence"},
    ]
    assert captured["text"] == {
        "format": {
            "type": "json_schema",
            "name": "hr_onboarding_result",
            "strict": True,
            "schema": schema,
        }
    }


@pytest.mark.asyncio
async def test_openai_ocr_sends_image_as_input_image(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.providers.openai as openai_provider

    _enable_openai(monkeypatch)
    captured: dict[str, object] = {}

    async def fake_post(payload):
        captured.update(payload)
        return {
            "output_text": json.dumps(
                {"ocr_text": "Synthetic text", "extracted_fields": {"clear_image": True}, "quality_notes": []}
            )
        }

    monkeypatch.setattr(openai_provider, "_post_responses", fake_post)

    result = await OpenAIProvider().extract(
        OCRRequest(file_name="sample.png", content=b"png", mime_type="image/png", material_type="resume")
    )

    file_part = captured["input"][0]["content"][1]
    assert file_part["type"] == "input_image"
    assert file_part["image_url"].startswith("data:image/png;base64,")
    assert captured["text"]["format"]["name"] == "hr_document_extraction"
    assert result.extracted_fields == {"clear_image": True}


@pytest.mark.asyncio
async def test_openai_ocr_sends_pdf_as_input_file(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.providers.openai as openai_provider

    _enable_openai(monkeypatch)
    captured: dict[str, object] = {}

    async def fake_post(payload):
        captured.update(payload)
        return {"output_text": '{"ocr_text":"PDF text","extracted_fields":{},"quality_notes":[]}'}

    monkeypatch.setattr(openai_provider, "_post_responses", fake_post)

    await OpenAIProvider().extract(
        OCRRequest(
            file_name="synthetic.pdf",
            content=b"%PDF-1.4",
            mime_type="application/pdf",
            material_type="degree_certificate",
        )
    )

    file_part = captured["input"][0]["content"][1]
    assert file_part["type"] == "input_file"
    assert file_part["filename"] == "synthetic.pdf"
    assert file_part["file_data"].startswith("data:application/pdf;base64,")


@pytest.mark.asyncio
async def test_openai_ocr_rejects_malformed_or_non_object_json(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.providers.openai as openai_provider

    _enable_openai(monkeypatch)

    async def malformed(_payload):
        return {"output_text": "not-json-with-private-document-text"}

    monkeypatch.setattr(openai_provider, "_post_responses", malformed)
    request = OCRRequest(file_name="sample.png", content=b"png", mime_type="image/png", material_type="resume")
    with pytest.raises(RuntimeError, match="invalid JSON"):
        await OpenAIProvider().extract(request)

    async def list_value(_payload):
        return {"output_text": "[]"}

    monkeypatch.setattr(openai_provider, "_post_responses", list_value)
    with pytest.raises(RuntimeError, match="non-object"):
        await OpenAIProvider().extract(request)


def test_openai_output_text_reads_nested_response_and_rejects_empty() -> None:
    response = {"output": [{"content": [{"type": "output_text", "text": "first"}, {"type": "text", "text": " second"}]}]}

    assert _output_text(response) == "first second"
    with pytest.raises(RuntimeError, match="empty"):
        _output_text({"output": []})


@pytest.mark.asyncio
async def test_openai_transport_uses_configured_endpoint_timeout_and_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.providers.openai as openai_provider

    _enable_openai(monkeypatch)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", "17")
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, *, timeout):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url, *, headers, json):
            captured.update({"url": url, "headers": headers, "json": json})
            return httpx.Response(200, json={"output_text": "ok"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(openai_provider.httpx, "AsyncClient", FakeClient)

    response = await _post_responses({"model": "gpt-test", "input": []})

    assert response == {"output_text": "ok"}
    assert captured["timeout"] == 17
    assert captured["url"] == "https://api.example.com/v1/responses"
    assert captured["headers"] == {"Authorization": "Bearer synthetic-test-key"}


@pytest.mark.asyncio
async def test_openai_transport_propagates_timeout_without_retrying(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.providers.openai as openai_provider

    _enable_openai(monkeypatch)
    attempts = 0

    class TimeoutClient:
        def __init__(self, *, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url, *, headers, json):
            nonlocal attempts
            attempts += 1
            raise httpx.ReadTimeout("synthetic timeout")

    monkeypatch.setattr(openai_provider.httpx, "AsyncClient", TimeoutClient)

    with pytest.raises(httpx.ReadTimeout):
        await _post_responses({"model": "gpt-test", "input": []})
    assert attempts == 1
