from datetime import date
from pathlib import Path

import pytest
import yaml
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.models.base import Base
from app.schemas.onboarding import CandidateCaseCreate
from app.services.onboarding_workflow import create_candidate_case
from app.services.workflow_config import WorkflowConfigError, get_active_workflow_steps, load_workflow_steps


def _write_workflow(path: Path, steps: str, *, extra_root: str = "") -> None:
    path.write_text(
        f"version: 1\nname: Test workflow\n{extra_root}steps:\n{steps}",
        encoding="utf-8",
    )


def test_default_workflow_yaml_matches_expected_nodes() -> None:
    steps = load_workflow_steps()

    assert steps[0].node_name == "background_check"
    assert steps[-1].node_name == "conversion_review"
    assert {step.node_name for step in steps if not step.candidate_visible} == {
        "probation_month_3",
        "probation_month_4",
    }


@pytest.mark.parametrize("template_name", ["default.yaml", "engineering.yaml"])
def test_example_workflows_make_deadlines_and_owners_explicit(template_name: str) -> None:
    template_path = Path("examples/workflows") / template_name
    document = yaml.safe_load(template_path.read_text(encoding="utf-8"))

    assert all("unit" in step and "owner_role" in step for step in document["steps"])


def test_custom_workflow_configures_created_nodes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workflow_path = tmp_path / "workflow.yaml"
    _write_workflow(
        workflow_path,
        "  - node_name: prepare_access\n"
        "    display_name: Prepare access\n"
        "    offset: -2\n"
        "  - node_name: manager_check_in\n"
        "    display_name: Manager check-in\n"
        "    offset: 2\n"
        "    unit: business_day\n"
        "    owner_role: manager\n",
    )
    monkeypatch.setenv("WORKFLOW_CONFIG_PATH", str(workflow_path))
    get_settings.cache_clear()
    get_active_workflow_steps.cache_clear()

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    try:
        with session_factory() as db:
            case = create_candidate_case(
                db,
                CandidateCaseCreate(candidate_name="Synthetic User", expected_onboard_date=date(2026, 8, 17)),
            )
            node_names = [node.node_name for node in case.nodes]
            due_dates = [node.due_date for node in case.nodes]
    finally:
        get_active_workflow_steps.cache_clear()
        get_settings.cache_clear()

    assert node_names == ["prepare_access", "manager_check_in"]
    assert due_dates == [date(2026, 8, 15), date(2026, 8, 19)]


@pytest.mark.parametrize(
    ("steps", "message"),
    [
        ("", "at least one step"),
        (
            "  - node_name: duplicate\n    display_name: One\n    offset: 0\n"
            "  - node_name: duplicate\n    display_name: Two\n    offset: 1\n",
            "duplicate",
        ),
        ("  - node_name: Bad-Name\n    display_name: Bad\n    offset: 0\n", "snake_case"),
        ("  - node_name: example\n    display_name: Example\n    offset: true\n", "offset"),
        ("  - node_name: example\n    display_name: Example\n    offset: 0\n    unit: fortnight\n", "unit"),
        ("  - node_name: example\n    display_name: Example\n    offset: 0\n    owner_role: candidate\n", "owner_role"),
        ("  - node_name: example\n    display_name: Example\n    offset: 0\n    unexpected: value\n", "unknown keys"),
    ],
)
def test_workflow_rejects_invalid_steps(tmp_path: Path, steps: str, message: str) -> None:
    workflow_path = tmp_path / "workflow.yaml"
    _write_workflow(workflow_path, steps)

    with pytest.raises(WorkflowConfigError, match=message):
        load_workflow_steps(workflow_path)


def test_workflow_rejects_unknown_root_key(tmp_path: Path) -> None:
    workflow_path = tmp_path / "workflow.yaml"
    _write_workflow(
        workflow_path,
        "  - node_name: example\n    display_name: Example\n    offset: 0\n",
        extra_root="private_policy: true\n",
    )

    with pytest.raises(WorkflowConfigError, match="unknown workflow keys"):
        load_workflow_steps(workflow_path)


def test_workflow_rejects_unsafe_yaml_tag(tmp_path: Path) -> None:
    workflow_path = tmp_path / "workflow.yaml"
    workflow_path.write_text("!!python/object/apply:os.system ['echo unsafe']\n", encoding="utf-8")

    with pytest.raises(WorkflowConfigError, match="safe YAML"):
        load_workflow_steps(workflow_path)


def test_workflow_rejects_missing_and_oversized_files(tmp_path: Path) -> None:
    with pytest.raises(WorkflowConfigError, match="does not exist"):
        load_workflow_steps(tmp_path / "missing.yaml")

    oversized = tmp_path / "oversized.yaml"
    oversized.write_bytes(b"x" * (128 * 1024 + 1))
    with pytest.raises(WorkflowConfigError, match="exceeds"):
        load_workflow_steps(oversized)
