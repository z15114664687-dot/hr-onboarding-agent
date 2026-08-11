from __future__ import annotations

import base64

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.routes import pages
from app.core.config import get_settings
from app.db.session import get_db
from app.models.base import Base
from app.models.onboarding import CandidateCase, MaterialSubmission
from app.services.demo_seed import seed_demo_data


def test_demo_mode_seeds_once_and_serves_both_workspaces(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("OCR_PROVIDER", "mock")
    monkeypatch.setenv("ALLOW_EXTERNAL_AI", "false")
    monkeypatch.setenv("UPLOAD_ROOT", str(tmp_path / "uploads"))
    get_settings.cache_clear()

    engine = create_engine(f"sqlite:///{tmp_path / 'demo.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    DemoSession = sessionmaker(bind=engine, expire_on_commit=False)
    with DemoSession() as db:
        assert seed_demo_data(db) == 3
        assert seed_demo_data(db) == 0
        assert db.query(CandidateCase).count() == 3
        assert db.query(MaterialSubmission).count() == 1

    app = FastAPI()
    app.include_router(pages.router)

    def demo_db():
        with DemoSession() as db:
            yield db

    app.dependency_overrides[get_db] = demo_db
    client = TestClient(app)

    hr = client.get("/ui/hr/workspace")
    candidate = client.get("/ui/candidate/1/workspace")
    assert hr.status_code == 200
    assert candidate.status_code == 200
    assert "林晓安" in hr.text
    assert "材料提交与审核反馈" in candidate.text

    upload = client.post(
        "/ui/candidate/1/workspace/upload",
        json={
            "material_type": "resume",
            "file_name": "synthetic_resume.pdf",
            "content_type": "application/pdf",
            "data_base64": base64.b64encode(b"%PDF-1.4\nsynthetic demo content").decode("ascii"),
        },
    )
    assert upload.status_code == 200
    assert upload.json()["status"] == "success"
    assert upload.json()["review_status"] in {"auto_approved", "manual_review"}
