from __future__ import annotations

from datetime import date, timedelta
import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.onboarding import CandidateCase, MaterialSubmission
from app.schemas.onboarding import CandidateCaseCreate
from app.services.onboarding_workflow import create_candidate_case, scan_overdue_nodes


DEMO_CANDIDATES_PATH = Path(__file__).resolve().parents[2] / "examples" / "demo_candidates.json"


def seed_demo_data(db: Session) -> int:
    """Seed deterministic synthetic records only in an empty demo database."""

    if not get_settings().demo_mode or db.query(CandidateCase).count():
        return 0
    records: list[dict[str, Any]] = json.loads(DEMO_CANDIDATES_PATH.read_text(encoding="utf-8"))
    today = date.today()
    created: list[CandidateCase] = []
    for record in records:
        case = create_candidate_case(
            db,
            CandidateCaseCreate(
                candidate_name=record["candidate_name"],
                email=record["email"],
                department=record["department"],
                position=record["position"],
                employment_type=record["employment_type"],
                city=record["city"],
                expected_onboard_date=today + timedelta(days=int(record["expected_onboard_offset_days"])),
            ),
        )
        created.append(case)

    first = created[0]
    for node in first.nodes[:2]:
        node.status = "approved"
    first.current_stage = "document_submission"
    first.overall_status = "in_progress"
    db.add(
        MaterialSubmission(
            case_id=first.id,
            material_type="resume",
            file_name="synthetic_resume.pdf",
            ocr_text="Synthetic resume for 林晓安 generated for demo mode.",
            extracted_fields_json=json.dumps({"姓名": "林晓安", "学校": "星河大学"}, ensure_ascii=False),
            review_status="auto_approved",
            review_reason="Deterministic mock review; no external AI call.",
        )
    )

    overdue = created[1]
    overdue.overall_status = "risk"
    overdue.risk_level = "high"
    scan_overdue_nodes(db, today=today)
    db.commit()
    return len(created)
