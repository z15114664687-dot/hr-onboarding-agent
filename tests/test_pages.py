import base64
import zipfile
from io import BytesIO
from xml.sax.saxutils import escape as xml_escape
from datetime import date, datetime
from collections.abc import Generator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_db
from app.main import app
from app.models.base import Base
from app.models.onboarding import MaterialSubmission
from app.api.routes import admin, pages
from app.core.config import get_settings
from app.schemas.onboarding import CandidateCaseCreate
from app.services import candidate_import
from app.services.onboarding_workflow import create_candidate_case


def test_hr_and_candidate_pages_render() -> None:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    with session_factory() as db:
        case = create_candidate_case(
            db,
            CandidateCaseCreate(
                candidate_name="张三",
                department="研发部",
                position="工程师",
                city="上海",
                expected_onboard_date=date(2026, 6, 1),
            ),
        )
        db.add(
            MaterialSubmission(
                case_id=case.id,
                material_type="resume",
                file_name="个人简历-张三.pdf",
                review_status="manual_review",
                review_reason="自动识别服务暂时不可用，已转人工复核",
                created_at=datetime(2026, 5, 27, 9, 0, 0),
            )
        )
        db.add(
            MaterialSubmission(
                case_id=case.id,
                material_type="badge_photo",
                file_name="最新工作证照片.png",
                review_status="auto_approved",
                review_reason="已通过",
                created_at=datetime(2026, 5, 27, 10, 0, 0),
            )
        )
        db.commit()
        case_id = case.id

    def override_get_db() -> Generator[Session, None, None]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            hr_response = client.get("/ui/hr")
            hr_workspace_response = client.get("/ui/hr/workspace")
            candidate_response = client.get(f"/ui/candidate/{case_id}")
            materials_response = client.get(f"/ui/candidate/{case_id}/materials")
            workspace_response = client.get(f"/ui/candidate/{case_id}/workspace")
    finally:
        app.dependency_overrides.clear()

    assert hr_response.status_code == 200
    assert "候选人总览" in hr_response.text
    assert "张三" in hr_response.text
    assert candidate_response.status_code == 200
    assert "流程重点" in candidate_response.text
    assert "材料状态" in candidate_response.text
    assert "node-confirm-form" in candidate_response.text
    assert 'class="panel material-status-panel collapsible-panel"' in candidate_response.text
    assert '<div class="candidate-material-list collapsible-list is-collapsed">' in candidate_response.text
    assert "syncCandidateMaterialPanel" in candidate_response.text
    assert f"/ui/candidate/{case_id}/materials" in candidate_response.text
    assert materials_response.status_code == 200
    assert "需上传至预入职系统" in materials_response.text
    assert "工作证照片" in materials_response.text
    assert "入职后关注事项" in materials_response.text
    assert '<section class="panel submitted-records">' in materials_response.text
    assert '<div class="submitted-records-list is-collapsed">' in materials_response.text
    assert '<span class="submitted-count">2 条</span>' in materials_response.text
    assert "个人简历-张三.pdf" in materials_response.text
    history_html = materials_response.text.split('<div class="submitted-records-list is-collapsed">', 1)[1]
    assert history_html.index("最新工作证照片.png") < history_html.index("个人简历-张三.pdf")
    assert "window.location.href = payload.redirect_url" not in materials_response.text
    assert "compressImageForUpload" in materials_response.text
    assert 'data-metric-key=\'approved\'' in materials_response.text
    assert "refreshMaterialMetrics" in materials_response.text
    assert "IMAGE_UPLOAD_TARGET_BYTES = 900 * 1024" in materials_response.text
    assert "IMAGE_UPLOAD_MIN_BYTES = 205 * 1024" in materials_response.text
    assert "blob.size < IMAGE_UPLOAD_MIN_BYTES" in materials_response.text
    assert "已加入审核队列" in materials_response.text
    assert "审核完成，请查看上方反馈。" in materials_response.text
    assert "已选 \" + files.length + \" 个文件" in materials_response.text
    assert "batch-upload" in materials_response.text
    assert "batch-submit" in materials_response.text
    assert "统一提交" in materials_response.text
    assert "加入待提交" in materials_response.text
    assert "submitBulkQueue" in materials_response.text
    assert "本次 \" + preparedFiles.length + \" 个文件审核完成" in materials_response.text
    assert "上传请求未完成，可能是网络中断或服务超时" in materials_response.text
    assert "noticeList.replaceChildren(notice)" in materials_response.text
    assert "hr-case-form" in hr_response.text
    assert "hr-inline-select" in hr_response.text
    assert "快捷操作" not in hr_response.text
    assert 'name="department"' in hr_response.text
    assert 'name="position"' in hr_response.text
    assert "/ui/hr/cases/" in hr_response.text
    assert hr_workspace_response.status_code == 200
    assert "HR 入职协同工作台" in hr_workspace_response.text
    assert "条线与岗位板块" not in hr_workspace_response.text
    assert "line-guide-card" not in hr_workspace_response.text
    assert "产品管理" in hr_workspace_response.text
    assert "IT 支持" in hr_workspace_response.text
    assert 'data-controls-positions="true"' in hr_workspace_response.text
    assert 'data-line-position-select="true"' in hr_workspace_response.text
    assert 'data-manual-save="true"' in hr_workspace_response.text
    assert 'class="hr-save-row"' in hr_workspace_response.text
    assert "syncLinePositionSelect" in hr_workspace_response.text
    assert "LINE_POSITION_OPTIONS" in hr_workspace_response.text
    assert "candidate-create-form" in hr_workspace_response.text
    assert "创建并同步" in hr_workspace_response.text
    assert "candidate-import-form" in hr_workspace_response.text
    assert "批量导入" in hr_workspace_response.text
    assert "/ui/hr/import-template" in hr_workspace_response.text
    assert 'data-delete-case="/api/admin/cases/' in hr_workspace_response.text
    assert "/api/admin/cases" in hr_workspace_response.text
    assert "候选人 open_id" in hr_workspace_response.text
    assert "候选人 union_id" not in hr_workspace_response.text
    assert 'name="feishu_union_id"' not in hr_workspace_response.text
    assert "建群前必须有候选人 open_id" in hr_workspace_response.text
    assert "缺候选人、HRBP/负责人 open_id" in hr_workspace_response.text
    assert workspace_response.status_code == 200
    assert "入职任务中心" in workspace_response.text
    assert "流程时间线" in workspace_response.text
    assert "材料提交与审核反馈" in workspace_response.text
    assert "当前要处理" in workspace_response.text
    assert "入职后关注事项" not in workspace_response.text
    assert "workspace-layout" in workspace_response.text
    assert "workspace-status-strip" in workspace_response.text
    assert 'id="material-resume"' in workspace_response.text
    assert '<section class="panel workspace-panel submitted-records workspace-submitted-records">' in workspace_response.text
    submitted_html = workspace_response.text.split(
        '<section class="panel workspace-panel submitted-records workspace-submitted-records">',
        1,
    )[1].split("</section>", 1)[0]
    assert "history-toggle" not in submitted_html
    assert 'class="submitted-records-list is-collapsed"' not in submitted_html
    assert "syncWorkspaceSubmittedRecords" in workspace_response.text
    assert "--workspace-submitted-list-height" in workspace_response.text
    assert "min(360px, 52vh)" in workspace_response.text
    assert "grid-template-columns: minmax(0, 1fr) auto auto;" in workspace_response.text
    assert "@media (max-width: 860px)" in workspace_response.text
    assert ".workspace-materials .upload-form {\n        grid-template-columns: 1fr;" in workspace_response.text
    assert 'id="node-probation_month_3"' not in workspace_response.text
    assert 'id="node-probation_month_4"' not in workspace_response.text
    assert 'id="node-probation_month_5"' in workspace_response.text
    assert 'id="node-probation_month_3"' not in candidate_response.text
    assert 'id="node-probation_month_4"' not in candidate_response.text
    assert 'window.location.pathname + (isBatch ? "/batch-upload" : "/upload")' in workspace_response.text
    assert "batch-submit" in workspace_response.text


def test_candidate_node_confirm_endpoint_updates_status_and_syncs(monkeypatch) -> None:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    sync_calls: list[int] = []

    async def fake_sync(case, db=None):
        sync_calls.append(case.id)
        return {"status": "skipped", "reason": "test"}

    monkeypatch.setattr(pages, "sync_case_to_bitable", fake_sync)

    with session_factory() as db:
        case = create_candidate_case(
            db,
            CandidateCaseCreate(
                candidate_name="确认节点",
                employment_type="社招",
                expected_onboard_date=date(2026, 6, 1),
            ),
        )
        case_id = case.id

    def override_get_db() -> Generator[Session, None, None]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            response = client.post(f"/ui/candidate/{case_id}/nodes/background_check/confirm")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["node_status"] == "submitted"
    assert response.json()["node_status_label"] == "已确认"
    assert sync_calls == [case_id]
    with session_factory() as db:
        case = db.query(pages.CandidateCase).filter(pages.CandidateCase.id == case_id).one()
        assert case.current_stage == "medical_check"


def test_admin_create_case_endpoint_rejects_duplicate_candidate(monkeypatch) -> None:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    async def fake_sync(case, db=None):
        return {"status": "skipped", "reason": "test"}

    async def fake_welcome(case):
        return {"status": "skipped", "reason": "test"}

    monkeypatch.setattr(admin, "_sync_case_to_bitable", fake_sync)
    monkeypatch.setattr(admin, "_notify_candidate_welcome", fake_welcome)

    def override_get_db() -> Generator[Session, None, None]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            payload = {
                "candidate_name": "重复候选人",
                "feishu_open_id": "ou_duplicate",
                "expected_onboard_date": "2026-06-01",
            }
            first = client.post("/api/admin/cases", json=payload)
            second = client.post("/api/admin/cases", json=payload)
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 200
    assert second.status_code == 409
    assert "疑似重复候选人" in second.json()["detail"]


def test_admin_create_case_endpoint_sends_candidate_welcome(monkeypatch) -> None:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    welcome_calls: list[str] = []

    async def fake_sync(case, db=None):
        return {"status": "skipped", "reason": "test"}

    async def fake_welcome(case):
        welcome_calls.append(case.feishu_open_id)
        return {"status": "success", "result": {"code": 0}}

    monkeypatch.setattr(admin, "_sync_case_to_bitable", fake_sync)
    monkeypatch.setattr(admin, "_notify_candidate_welcome", fake_welcome)

    def override_get_db() -> Generator[Session, None, None]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/admin/cases",
                json={
                    "candidate_name": "欢迎候选人",
                    "feishu_open_id": "ou_candidate",
                    "expected_onboard_date": "2026-06-01",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["welcome_message"]["status"] == "success"
    assert welcome_calls == ["ou_candidate"]


def test_admin_import_cases_from_excel_resolves_candidate_open_id(monkeypatch) -> None:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    sync_calls: list[int] = []

    async def fake_sync(case, db=None):
        sync_calls.append(case.id)
        return {"status": "skipped", "reason": "test"}

    async def fake_welcome(case):
        return {"status": "success", "result": {"code": 0}}

    async def fake_lookup(*, emails=None, mobiles=None):
        return {"000-0000-0000": "ou_auto_candidate", "new@example.com": "ou_auto_candidate"}

    monkeypatch.setattr(admin, "_sync_case_to_bitable", fake_sync)
    monkeypatch.setattr(admin, "_notify_candidate_welcome", fake_welcome)
    monkeypatch.setattr(candidate_import, "batch_get_open_ids", fake_lookup)

    def override_get_db() -> Generator[Session, None, None]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    rows = [
        [
            "候选人姓名",
            "预计入职日期",
            "条线",
            "岗位",
            "城市",
            "入职类型",
            "职级",
            "候选人手机号",
            "候选人邮箱",
            "候选人 open_id",
            "HRBP open_id",
            "负责人 open_id",
        ],
        ["批量候选人", "2026-06-08", "组织支持", "IT 支持", "上海", "社招", "P1", "000-0000-0000", "new@example.com", "", "ou_hr", "ou_manager"],
    ]
    file_bytes = _minimal_xlsx(rows)

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/admin/cases/import-excel",
                json={"file_name": "候选人导入.xlsx", "data_base64": base64.b64encode(file_bytes).decode("ascii")},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["created_count"] == 1
    assert response.json()["created"][0]["welcome_message"]["status"] == "success"
    assert response.json()["skipped_count"] == 0
    assert response.json()["error_count"] == 0
    assert sync_calls == [1]
    with session_factory() as db:
        case = db.query(pages.CandidateCase).filter(pages.CandidateCase.candidate_name == "批量候选人").one()
        assert case.feishu_open_id == "ou_auto_candidate"
        assert case.department == "组织支持"
        assert case.position == "IT 支持"


def test_admin_delete_case_removes_local_candidate(monkeypatch) -> None:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    with session_factory() as db:
        case = create_candidate_case(
            db,
            CandidateCaseCreate(
                candidate_name="待删除候选人",
                expected_onboard_date=date(2026, 6, 1),
            ),
        )
        case_id = case.id

    def override_get_db() -> Generator[Session, None, None]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            response = client.delete(f"/api/admin/cases/{case_id}")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    with session_factory() as db:
        assert db.query(pages.CandidateCase).filter(pages.CandidateCase.id == case_id).one_or_none() is None


def test_hr_case_status_endpoint_updates_node_and_syncs(monkeypatch) -> None:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    sync_calls: list[int] = []

    async def fake_sync(case, db=None):
        sync_calls.append(case.id)
        return {"status": "skipped", "reason": "test"}

    monkeypatch.setattr(pages, "sync_case_to_bitable", fake_sync)

    with session_factory() as db:
        case = create_candidate_case(
            db,
            CandidateCaseCreate(
                candidate_name="后台改标",
                employment_type="社招",
                expected_onboard_date=date(2026, 6, 1),
            ),
        )
        case_id = case.id

    def override_get_db() -> Generator[Session, None, None]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/ui/hr/cases/{case_id}/status",
                json={
                    "current_stage": "medical_check",
                    "node_status": "approved",
                    "overall_status": "in_progress",
                    "risk_level": "low",
                    "department": "组织支持",
                    "position": "IT 支持",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["node_status"] == "approved"
    assert sync_calls == [case_id]
    with session_factory() as db:
        case = db.query(pages.CandidateCase).filter(pages.CandidateCase.id == case_id).one()
        node = next(node for node in case.nodes if node.node_name == "medical_check")
        assert node.status == "approved"
        assert node.completed_at is not None
        assert case.department == "组织支持"
        assert case.position == "IT 支持"


def test_candidate_material_upload_rejects_disallowed_extension(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("FEISHU_BASE_APP_TOKEN", "")
    monkeypatch.setenv("FEISHU_MATERIAL_TABLE_ID", "")
    monkeypatch.setenv("FEISHU_APP_ID", "")
    monkeypatch.setenv("FEISHU_APP_SECRET", "")
    monkeypatch.setattr(pages, "UPLOAD_ROOT", tmp_path)
    get_settings.cache_clear()

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    with session_factory() as db:
        case = create_candidate_case(
            db,
            CandidateCaseCreate(
                candidate_name="李四",
                employment_type="社招",
                expected_onboard_date=date(2026, 6, 1),
            ),
        )
        case_id = case.id

    def override_get_db() -> Generator[Session, None, None]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/ui/candidate/{case_id}/materials/upload",
                json={
                    "material_type": "medical_report",
                    "file_name": "体检报告.txt",
                    "data_base64": base64.b64encode(b"not a pdf").decode("ascii"),
                },
            )
            detail_response = client.get(f"/ui/candidate/{case_id}/materials?uploaded=medical_report")
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()

    assert response.status_code == 400
    assert "extension is not allowed" in response.json()["detail"]
    assert detail_response.status_code == 200
    with session_factory() as db:
        assert db.query(MaterialSubmission).filter(MaterialSubmission.case_id == case_id).count() == 0


def test_candidate_material_batch_upload_rejects_disallowed_files(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("FEISHU_BASE_APP_TOKEN", "")
    monkeypatch.setenv("FEISHU_MATERIAL_TABLE_ID", "")
    monkeypatch.setenv("FEISHU_APP_ID", "")
    monkeypatch.setenv("FEISHU_APP_SECRET", "")
    monkeypatch.setattr(pages, "UPLOAD_ROOT", tmp_path)
    get_settings.cache_clear()

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    with session_factory() as db:
        case = create_candidate_case(
            db,
            CandidateCaseCreate(
                candidate_name="王五",
                employment_type="社招",
                expected_onboard_date=date(2026, 6, 1),
            ),
        )
        case_id = case.id

    def override_get_db() -> Generator[Session, None, None]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/ui/candidate/{case_id}/materials/batch-upload",
                json={
                    "material_type": "medical_report",
                    "files": [
                        {
                            "file_name": "体检报告1.txt",
                            "data_base64": base64.b64encode(b"not a pdf").decode("ascii"),
                        },
                        {
                            "file_name": "体检报告2.txt",
                            "data_base64": base64.b64encode(b"still not a pdf").decode("ascii"),
                        },
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()

    assert response.status_code == 400
    assert "extension is not allowed" in response.json()["detail"]
    with session_factory() as db:
        materials = db.query(MaterialSubmission).filter(MaterialSubmission.case_id == case_id).all()
        assert materials == []


def test_candidate_material_multi_batch_submit_sends_one_summary(monkeypatch) -> None:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    calls: list[dict[str, object]] = []
    notifications: list[list[dict[str, object]]] = []
    sync_calls: list[int] = []

    async def fake_record_material_upload_file(
        db,
        case,
        *,
        material_type,
        file_name,
        content_type,
        data_base64,
        notify_candidate,
        sync_candidate=True,
    ):
        calls.append(
            {
                "material_type": material_type,
                "file_name": file_name,
                "notify_candidate": notify_candidate,
                "sync_candidate": sync_candidate,
            }
        )
        status = "auto_approved" if material_type == "resume" else "manual_review"
        return {
            "status": "success",
            "material_type": material_type,
            "material_name": pages._material_name(material_type),
            "review_status": status,
            "review_label": pages._label(status),
            "message": "材料已通过自动审核。" if status == "auto_approved" else "材料已收到，将由 HR 人工审核。",
            "file_name": file_name,
            "review_reason": "材料已通过自动审核。" if status == "auto_approved" else "材料已收到，将由 HR 人工审核。",
            "record_html": "<article class=\"item\"></article>",
            "_raw_result": {"material_type": material_type, "file_name": file_name},
        }

    async def fake_notify(case, *, groups):
        notifications.append(groups)
        return {"status": "success", "result": "sent"}

    async def fake_candidate_sync(case):
        sync_calls.append(case.id)
        return {"status": "success", "action": "updated"}

    monkeypatch.setattr(pages, "_record_material_upload_file", fake_record_material_upload_file)
    monkeypatch.setattr(pages, "notify_candidate_material_overall_summary", fake_notify)
    monkeypatch.setattr(pages, "sync_candidate_material_summary_to_bitable", fake_candidate_sync)

    with session_factory() as db:
        case = create_candidate_case(
            db,
            CandidateCaseCreate(
                candidate_name="统一提交",
                employment_type="校招-境内",
                expected_onboard_date=date(2026, 6, 1),
            ),
        )
        case_id = case.id

    def override_get_db() -> Generator[Session, None, None]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/ui/candidate/{case_id}/materials/batch-submit",
                json={
                    "items": [
                        {
                            "material_type": "resume",
                            "files": [{"file_name": "简历.pdf", "data_base64": base64.b64encode(b"resume").decode("ascii")}],
                        },
                        {
                            "material_type": "badge_photo",
                            "files": [{"file_name": "照片.png", "data_base64": base64.b64encode(b"photo").decode("ascii")}],
                        },
                    ]
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary_title"] == "材料统一提交"
    assert payload["material_count"] == 2
    assert payload["file_count"] == 2
    assert payload["review_status"] == "manual_review"
    assert "本次共提交 2 个材料项、2 个文件" in payload["message"]
    assert len(payload["groups"]) == 2
    assert all(call["notify_candidate"] is False for call in calls)
    assert all(call["sync_candidate"] is False for call in calls)
    assert len(notifications) == 1
    assert len(notifications[0]) == 2
    assert sync_calls == [case_id]


def test_material_supplement_status_renders_separately_from_rejection() -> None:
    material = MaterialSubmission(
        material_type="award_certificate",
        review_status="auto_rejected",
        review_reason="本次获奖证明已通过单份材料检查，但还缺：数学建模二等奖。请继续上传缺少的文件。",
    )

    assert pages._material_status_key(material) == "needs_supplement"
    assert "材料需补充" in pages._material_item(material, compact=False)


def test_batch_upload_summary_counts_supplement_as_file_passed() -> None:
    summary = pages._batch_upload_summary(
        "award_certificate",
        [
            {
                "review_status": "needs_supplement",
                "review_reason": "本次获奖证明已通过单份材料检查，但还缺：荣获北桥大学入学奖学金。请继续上传缺少的文件。",
            },
            {
                "review_status": "auto_approved",
                "review_reason": "材料已通过自动审核。",
            },
        ],
    )

    assert summary["review_status"] == "needs_supplement"
    assert "文件已通过 2 个" in summary["message"]
    assert "仍需补充 1 项" in summary["message"]
    assert "未通过 0 个" in summary["message"]
    assert "荣获北桥大学入学奖学金" in summary["message"]


def _minimal_xlsx(rows: list[list[str]]) -> bytes:
    def cell_ref(row_index: int, col_index: int) -> str:
        col = ""
        value = col_index
        while value:
            value, remainder = divmod(value - 1, 26)
            col = chr(ord("A") + remainder) + col
        return f"{col}{row_index}"

    row_xml = []
    for row_index, row in enumerate(rows, start=1):
        cell_xml = []
        for col_index, value in enumerate(row, start=1):
            cell_xml.append(
                f'<c r="{cell_ref(row_index, col_index)}" t="inlineStr"><is><t>{xml_escape(value)}</t></is></c>'
            )
        row_xml.append(f'<row r="{row_index}">{"".join(cell_xml)}</row>')
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(row_xml)}</sheetData>'
        "</worksheet>"
    )
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            "</Types>",
        )
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="候选人导入" sheetId="1" r:id="rId1"/></sheets>'
            "</workbook>",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>",
        )
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return output.getvalue()
