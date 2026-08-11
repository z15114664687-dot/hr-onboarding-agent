from __future__ import annotations

import base64
import time
from types import SimpleNamespace

from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.routes.feishu_events import router as feishu_router
from app.api.routes.pages import (
    _candidate_table,
    _decode_upload_data,
    _safe_file_name,
    _upload_path,
    _validate_upload_content,
)
from app.core.config import get_settings
from app.core.security import (
    issue_candidate_token,
    require_candidate_access,
    require_hr_access,
    require_same_origin,
    verify_candidate_token,
    verify_feishu_event_freshness,
    verify_feishu_token,
)
from app.db.session import get_db
from app.models.base import Base
from app.models.onboarding import ProcessEventLog
from app.utils.idempotency import claim_event


def _secure_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.setenv("APP_SESSION_SECRET", "synthetic-test-secret-that-is-long-enough")
    monkeypatch.setenv("HR_ADMIN_USERNAME", "demo-admin")
    monkeypatch.setenv("HR_ADMIN_PASSWORD", "synthetic-password")
    get_settings.cache_clear()


def test_hr_dependency_requires_valid_basic_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    _secure_settings(monkeypatch)
    app = FastAPI()

    @app.get("/hr", dependencies=[Depends(require_hr_access)])
    def hr() -> dict[str, str]:
        return {"status": "ok"}

    client = TestClient(app)
    assert client.get("/hr").status_code == 401
    assert client.get("/hr", auth=("demo-admin", "wrong")).status_code == 401
    assert client.get("/hr", auth=("demo-admin", "synthetic-password")).status_code == 200


def test_candidate_token_is_case_bound_and_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    _secure_settings(monkeypatch)
    token = issue_candidate_token(7, expires_at=2_000_000_000)

    assert verify_candidate_token(7, token, now=1_999_999_999)
    assert not verify_candidate_token(8, token, now=1_999_999_999)
    assert not verify_candidate_token(7, token, now=2_000_000_001)


def test_candidate_access_returns_not_found_for_invalid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _secure_settings(monkeypatch)
    app = FastAPI()

    @app.get("/candidate/{case_id}")
    def candidate(case_id: int, request: Request) -> dict[str, bool]:
        require_candidate_access(request, case_id)
        return {"ok": True}

    client = TestClient(app)
    assert client.get("/candidate/1").status_code == 404
    token = issue_candidate_token(1)
    assert client.get(f"/candidate/1?access_token={token}").status_code == 200
    assert client.get(f"/candidate/2?access_token={token}").status_code == 404


def test_feishu_auth_fails_closed_and_rejects_stale_events(monkeypatch: pytest.MonkeyPatch) -> None:
    _secure_settings(monkeypatch)
    assert not verify_feishu_token(None)

    monkeypatch.setenv("FEISHU_VERIFICATION_TOKEN", "synthetic-feishu-token")
    get_settings.cache_clear()
    assert verify_feishu_token("synthetic-feishu-token")
    assert not verify_feishu_token("wrong")
    now = int(time.time())
    assert verify_feishu_event_freshness({"create_time": str(now)}, now=now)
    assert not verify_feishu_event_freshness({"create_time": str(now - 301)}, now=now)
    assert not verify_feishu_event_freshness({}, now=now)


def test_upload_rejects_mime_extension_mismatch() -> None:
    with pytest.raises(Exception, match="MIME type"):
        _validate_upload_content("resume", "resume.pdf", "text/html", b"%PDF-synthetic")
    with pytest.raises(Exception, match="content does not match"):
        _validate_upload_content("resume", "resume.pdf", "application/pdf", b"<script>alert(1)</script>")


def test_upload_size_and_path_are_confined(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "4")
    monkeypatch.setenv("UPLOAD_ROOT", str(tmp_path / "uploads"))
    get_settings.cache_clear()

    with pytest.raises(Exception, match="exceeds"):
        _decode_upload_data(base64.b64encode(b"12345").decode("ascii"))
    assert _safe_file_name("../../candidate<script>.pdf") == "candidate_script_.pdf"
    path = _upload_path(1, "../../resume", "safe.pdf")
    assert path.is_relative_to((tmp_path / "uploads").resolve())


def test_browser_state_change_rejects_cross_site_origin() -> None:
    app = FastAPI()

    @app.post("/change")
    def change(request: Request) -> dict[str, bool]:
        require_same_origin(request)
        return {"ok": True}

    client = TestClient(app)
    assert client.post("/change", headers={"Origin": "http://testserver"}).status_code == 200
    assert client.post("/change", headers={"Origin": "https://attacker.example"}).status_code == 403


def test_candidate_table_escapes_stored_html() -> None:
    case = SimpleNamespace(
        id=1,
        candidate_name="<script>alert(1)</script>",
        department="<img src=x onerror=alert(1)>",
        position="Developer",
        city="<svg onload=alert(1)>",
        expected_onboard_date=None,
        current_stage="medical_check",
        overall_status="pending",
        risk_level="low",
        nodes=[],
        materials=[],
    )

    rendered = _candidate_table([case])

    assert "<script>" not in rendered
    assert "<svg onload" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "&lt;svg onload=alert(1)&gt;" in rendered


def test_feishu_http_callback_requires_auth_and_freshness(monkeypatch: pytest.MonkeyPatch) -> None:
    _secure_settings(monkeypatch)
    monkeypatch.setenv("FEISHU_VERIFICATION_TOKEN", "synthetic-feishu-token")
    get_settings.cache_clear()
    app = FastAPI()
    app.include_router(feishu_router)

    def empty_db():
        yield None

    app.dependency_overrides[get_db] = empty_db
    client = TestClient(app)

    assert client.post("/api/feishu/events", json={"challenge": "ok", "token": "wrong"}).status_code == 403
    challenge = client.post(
        "/api/feishu/events",
        json={"challenge": "synthetic-challenge", "token": "synthetic-feishu-token"},
    )
    assert challenge.status_code == 200
    assert challenge.json() == {"challenge": "synthetic-challenge"}

    stale = client.post(
        "/api/feishu/events",
        json={
            "token": "synthetic-feishu-token",
            "header": {"event_id": "evt-stale", "event_type": "synthetic.event", "create_time": "1"},
            "event": {},
        },
    )
    assert stale.status_code == 403

    current = client.post(
        "/api/feishu/events",
        json={
            "token": "synthetic-feishu-token",
            "header": {
                "event_id": "evt-current",
                "event_type": "synthetic.event",
                "create_time": str(int(time.time())),
            },
            "event": {},
        },
    )
    assert current.status_code == 200
    assert current.json() == {"code": 0, "msg": "success"}


def test_idempotency_does_not_store_raw_payload_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _secure_settings(monkeypatch)
    monkeypatch.setenv("STORE_EVENT_PAYLOADS", "false")
    get_settings.cache_clear()
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        assert claim_event(
            db,
            external_event_id="synthetic-event-id",
            event_type="synthetic.event",
            payload={"secret": "must-not-be-stored"},
        )
        stored = db.query(ProcessEventLog).one()
        assert stored.payload_json is None
