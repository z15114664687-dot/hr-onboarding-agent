import base64

from fastapi.testclient import TestClient

from app.api.routes import gemini_gateway
from app.core.config import get_settings
from app.main import app


def test_gemini_gateway_requires_token(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_GATEWAY_TOKEN", "secret")
    get_settings.cache_clear()

    with TestClient(app) as client:
        response = client.post("/internal/gemini/generate", json={"prompt": "hi"})

    assert response.status_code == 401


def test_gemini_gateway_generates_with_authorized_token(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_GATEWAY_TOKEN", "secret")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    get_settings.cache_clear()

    async def fake_call_gemini(*args, **kwargs):
        return "北京最低工资标准为2540元 [S1]。", [{"id": "G1", "title": "source", "path": "https://example.com"}]

    monkeypatch.setattr(gemini_gateway, "_call_gemini", fake_call_gemini)

    with TestClient(app) as client:
        response = client.post(
            "/internal/gemini/generate",
            headers={"Authorization": "Bearer secret"},
            json={"prompt": "北京最低工资是多少", "enable_google_search": True},
        )

    assert response.status_code == 200
    assert response.json() == {
        "answer": "北京最低工资标准为2540元 [S1]。",
        "sources": [{"id": "G1", "title": "source", "path": "https://example.com"}],
    }


def test_gemini_gateway_ocr_returns_controlled_error(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_GATEWAY_TOKEN", "secret")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    get_settings.cache_clear()

    async def failing_ocr(*args, **kwargs):
        raise RuntimeError("low level stack detail")

    monkeypatch.setattr(gemini_gateway, "extract_material_with_gemini_content", failing_ocr)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/internal/gemini/ocr",
                headers={"Authorization": "Bearer secret"},
                json={
                    "file_name": "证书.pdf",
                    "content_base64": base64.b64encode(b"pdf").decode("ascii"),
                    "mime_type": "application/pdf",
                    "material_type": "degree_certificate",
                },
            )
    finally:
        get_settings.cache_clear()

    assert response.status_code == 502
    assert response.json()["detail"] == "Gemini OCR service failed"
    assert "low level stack detail" not in response.text
