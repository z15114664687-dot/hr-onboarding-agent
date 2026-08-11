from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re
from typing import Literal

import yaml


DeadlineUnit = Literal["calendar_day", "business_day", "month"]
OwnerRole = Literal["hrbp", "manager", "it_admin", "party_admin", "compliance"]

DEFAULT_WORKFLOW_PATH = "examples/workflows/default.yaml"
MAX_WORKFLOW_BYTES = 128 * 1024
MAX_WORKFLOW_STEPS = 64
PROJECT_ROOT = Path(__file__).resolve().parents[2]

_NODE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ALLOWED_ROOT_KEYS = {"version", "name", "description", "steps"}
_ALLOWED_STEP_KEYS = {
    "node_name",
    "display_name",
    "offset",
    "unit",
    "description",
    "owner_role",
    "group_name",
    "candidate_visible",
}
_DEADLINE_UNITS = {"calendar_day", "business_day", "month"}
_OWNER_ROLES = {"hrbp", "manager", "it_admin", "party_admin", "compliance"}


class WorkflowConfigError(ValueError):
    """Raised when a workflow YAML file is unsafe or invalid."""


@dataclass(frozen=True)
class WorkflowStep:
    node_name: str
    display_name: str
    offset: int
    unit: DeadlineUnit = "calendar_day"
    description: str = ""
    owner_role: OwnerRole = "hrbp"
    group_name: str | None = None
    candidate_visible: bool = True


def load_workflow_steps(configured_path: str | Path = DEFAULT_WORKFLOW_PATH) -> tuple[WorkflowStep, ...]:
    path = _resolve_path(configured_path)
    if not path.is_file():
        raise WorkflowConfigError(f"workflow configuration file does not exist: {configured_path}")
    if path.stat().st_size > MAX_WORKFLOW_BYTES:
        raise WorkflowConfigError(f"workflow configuration exceeds {MAX_WORKFLOW_BYTES} bytes")
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise WorkflowConfigError("workflow configuration is not valid safe YAML") from exc
    return _parse_document(document)


@lru_cache(maxsize=1)
def get_active_workflow_steps() -> tuple[WorkflowStep, ...]:
    """Load the process-wide workflow selected before application startup."""

    from app.core.config import get_settings

    return load_workflow_steps(get_settings().workflow_config_path)


def _resolve_path(configured_path: str | Path) -> Path:
    path = Path(configured_path).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def _parse_document(document: object) -> tuple[WorkflowStep, ...]:
    if not isinstance(document, dict):
        raise WorkflowConfigError("workflow configuration must be a mapping")
    extra_root_keys = set(document) - _ALLOWED_ROOT_KEYS
    if extra_root_keys:
        raise WorkflowConfigError(f"unknown workflow keys: {', '.join(sorted(extra_root_keys))}")
    if document.get("version") != 1:
        raise WorkflowConfigError("workflow configuration version must be 1")
    steps = document.get("steps")
    if not isinstance(steps, list) or not steps:
        raise WorkflowConfigError("workflow configuration must contain at least one step")
    if len(steps) > MAX_WORKFLOW_STEPS:
        raise WorkflowConfigError(f"workflow configuration cannot exceed {MAX_WORKFLOW_STEPS} steps")

    parsed_steps = tuple(_parse_step(raw_step, index) for index, raw_step in enumerate(steps, start=1))
    node_names = [step.node_name for step in parsed_steps]
    duplicates = sorted({node_name for node_name in node_names if node_names.count(node_name) > 1})
    if duplicates:
        raise WorkflowConfigError(f"duplicate workflow node_name values: {', '.join(duplicates)}")
    return parsed_steps


def _parse_step(raw_step: object, index: int) -> WorkflowStep:
    if not isinstance(raw_step, dict):
        raise WorkflowConfigError(f"workflow step {index} must be a mapping")
    extra_keys = set(raw_step) - _ALLOWED_STEP_KEYS
    if extra_keys:
        raise WorkflowConfigError(f"workflow step {index} has unknown keys: {', '.join(sorted(extra_keys))}")

    node_name = _required_string(raw_step, "node_name", index, max_length=64)
    if not _NODE_NAME_PATTERN.fullmatch(node_name):
        raise WorkflowConfigError(f"workflow step {index} node_name must use lowercase snake_case")
    display_name = _required_string(raw_step, "display_name", index, max_length=80)
    offset = raw_step.get("offset")
    if isinstance(offset, bool) or not isinstance(offset, int) or not -365 <= offset <= 365:
        raise WorkflowConfigError(f"workflow step {index} offset must be an integer from -365 to 365")
    unit = raw_step.get("unit", "calendar_day")
    if unit not in _DEADLINE_UNITS:
        raise WorkflowConfigError(f"workflow step {index} unit is unsupported")
    owner_role = raw_step.get("owner_role", "hrbp")
    if owner_role not in _OWNER_ROLES:
        raise WorkflowConfigError(f"workflow step {index} owner_role is unsupported")
    description = _optional_string(raw_step, "description", index, max_length=500) or ""
    group_name = _optional_string(raw_step, "group_name", index, max_length=80)
    candidate_visible = raw_step.get("candidate_visible", True)
    if not isinstance(candidate_visible, bool):
        raise WorkflowConfigError(f"workflow step {index} candidate_visible must be true or false")

    return WorkflowStep(
        node_name=node_name,
        display_name=display_name,
        offset=offset,
        unit=unit,
        description=description,
        owner_role=owner_role,
        group_name=group_name,
        candidate_visible=candidate_visible,
    )


def _required_string(raw_step: dict, field: str, index: int, *, max_length: int) -> str:
    value = _optional_string(raw_step, field, index, max_length=max_length)
    if value is None:
        raise WorkflowConfigError(f"workflow step {index} {field} is required")
    return value


def _optional_string(raw_step: dict, field: str, index: int, *, max_length: int) -> str | None:
    value = raw_step.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > max_length:
        raise WorkflowConfigError(f"workflow step {index} {field} must be a non-empty string up to {max_length} characters")
    return value.strip()
