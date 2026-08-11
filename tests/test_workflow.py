from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.schemas.onboarding import CandidateCaseCreate
from app.services.onboarding_workflow import (
    ACTIVE_WORKFLOW,
    NODE_DISPLAY_NAMES,
    DuplicateCandidateError,
    confirm_node_by_candidate,
    create_candidate_case,
    get_progress_for_open_id,
    scan_overdue_nodes,
)


def test_create_case_generates_default_nodes() -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    with session_factory() as db:
        case = create_candidate_case(
            db,
            CandidateCaseCreate(
                candidate_name="张三",
                mobile="000-0000-0000",
                email="zhangsan@example.com",
                feishu_open_id="ou_test",
                department="研发部",
                position="后端工程师",
                employment_type="社招",
                city="上海",
                expected_onboard_date=date(2026, 6, 1),
            ),
        )

        assert case.id is not None
        assert len(case.nodes) == len(ACTIVE_WORKFLOW)
        assert {node.node_name for node in case.nodes} == {step.node_name for step in ACTIVE_WORKFLOW}
        assert case.nodes[0].node_name == "background_check"
        assert case.nodes[0].due_date == date(2026, 5, 25)
        assert case.nodes[1].node_name == "medical_check"
        assert case.nodes[1].due_date == date(2026, 5, 29)
        due_dates = {node.node_name: node.due_date for node in case.nodes}
        assert due_dates["conflict_of_interest_declaration"] == date(2026, 6, 8)
        assert due_dates["role_qualification_registration"] == date(2026, 6, 4)
        assert case.nodes[-1].node_name == "conversion_review"
        assert case.nodes[-1].due_date == date(2026, 12, 1)
        assert NODE_DISPLAY_NAMES["probation_month_3"] == "试用期考察"
        assert NODE_DISPLAY_NAMES["conversion_review"] == "转正考核"


def test_create_case_rejects_duplicate_open_id() -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    with session_factory() as db:
        create_candidate_case(
            db,
            CandidateCaseCreate(
                candidate_name="张三",
                feishu_open_id="ou_same",
                expected_onboard_date=date(2026, 6, 1),
            ),
        )

        with pytest.raises(DuplicateCandidateError, match="open_id"):
            create_candidate_case(
                db,
                CandidateCaseCreate(
                    candidate_name="张三",
                    feishu_open_id="ou_same",
                    expected_onboard_date=date(2026, 6, 2),
                ),
            )


def test_create_case_rejects_exact_same_candidate_profile() -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    with session_factory() as db:
        payload = CandidateCaseCreate(
            candidate_name="李四",
            department="产品与技术",
            position="产品管理",
            expected_onboard_date=date(2026, 6, 1),
        )
        create_candidate_case(db, payload)

        with pytest.raises(DuplicateCandidateError, match="姓名、预计入职日期、条线和岗位"):
            create_candidate_case(db, payload)


def test_progress_groups_material_submission_subnodes() -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    with session_factory() as db:
        create_candidate_case(
            db,
            CandidateCaseCreate(
                candidate_name="李四",
                feishu_open_id="ou_material_group",
                employment_type="校招-境内",
                expected_onboard_date=date(2026, 6, 1),
            ),
        )

        progress = get_progress_for_open_id(db, "ou_material_group")

        assert "- 材料递交：" in progress
        assert "  - 预入职材料：" in progress
        assert "  - 利益冲突申报：" in progress
        assert "  - 岗位资格登记：" in progress
        assert "  - 人事档案转移：" in progress


def test_candidate_progress_hides_internal_probation_email_nodes() -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    with session_factory() as db:
        create_candidate_case(
            db,
            CandidateCaseCreate(
                candidate_name="试用期候选人",
                feishu_open_id="ou_probation",
                expected_onboard_date=date(2026, 6, 1),
            ),
        )

        progress = get_progress_for_open_id(db, "ou_probation")

        assert "2026-09-01" not in progress
        assert "2026-10-01" not in progress
        assert "2026-11-01" in progress


def test_candidate_confirmation_advances_current_stage_and_clears_overdue() -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    with session_factory() as db:
        case = create_candidate_case(
            db,
            CandidateCaseCreate(
                candidate_name="王五",
                feishu_open_id="ou_confirm",
                employment_type="社招",
                expected_onboard_date=date(2026, 6, 1),
            ),
        )
        background = next(node for node in case.nodes if node.node_name == "background_check")
        background.status = "overdue"
        background.overdue_days = 5
        background.escalation_level = 2
        case.overall_status = "risk"
        case.risk_level = "high"
        db.commit()

        node = confirm_node_by_candidate(db, case.id, "background_check")
        db.refresh(case)

        assert node.status == "submitted"
        assert node.overdue_days == 0
        assert node.escalation_level == 0
        assert case.current_stage == "medical_check"
        assert case.overall_status == "in_progress"
        assert case.risk_level == "low"


def test_submitted_nodes_are_not_scanned_as_overdue() -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    with session_factory() as db:
        case = create_candidate_case(
            db,
            CandidateCaseCreate(
                candidate_name="赵六",
                employment_type="社招",
                expected_onboard_date=date(2026, 6, 1),
            ),
        )
        background = next(node for node in case.nodes if node.node_name == "background_check")
        background.status = "submitted"
        db.commit()

        changed = scan_overdue_nodes(db, today=date(2026, 6, 10))

        assert background.id not in {node.id for node in changed}
