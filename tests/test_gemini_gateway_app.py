from fastapi.testclient import TestClient

from app.gemini_gateway_app import app
from app.gemini_gateway_server import main as gateway_server_main


def test_gemini_gateway_app_health() -> None:
    with TestClient(app) as client:
        root_response = client.get("/")
        health_response = client.get("/internal/gemini/health")

    assert root_response.status_code == 200
    assert root_response.json()["service"] == "hr-gemini-gateway"
    assert health_response.status_code == 200
    assert health_response.json()["status"] == "ok"


def test_gemini_gateway_server_uses_port_env(monkeypatch) -> None:
    calls = {}

    def fake_run(app_path: str, *, host: str, port: int) -> None:
        calls["app_path"] = app_path
        calls["host"] = host
        calls["port"] = port

    monkeypatch.setenv("PORT", "9021")
    monkeypatch.setattr("app.gemini_gateway_server.uvicorn.run", fake_run)

    gateway_server_main()

    assert calls == {
        "app_path": "app.gemini_gateway_app:app",
        "host": "0.0.0.0",
        "port": 9021,
    }
