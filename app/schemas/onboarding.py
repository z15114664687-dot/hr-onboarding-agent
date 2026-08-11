from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


OverallStatus = Literal["pending", "in_progress", "risk", "completed", "terminated"]
RiskLevel = Literal["low", "medium", "high"]
NodeStatus = Literal["not_started", "pending", "submitted", "approved", "rejected", "overdue", "skipped"]


class CandidateCaseCreate(BaseModel):
    candidate_name: str
    mobile: Optional[str] = None
    email: Optional[EmailStr] = None
    feishu_open_id: Optional[str] = None
    feishu_union_id: Optional[str] = None
    department: Optional[str] = None
    position: Optional[str] = None
    employment_type: Optional[str] = None
    job_level: Optional[str] = None
    city: Optional[str] = None
    hrbp_open_id: Optional[str] = None
    manager_open_id: Optional[str] = None
    expected_onboard_date: date


class CandidateCaseRead(CandidateCaseCreate):
    id: int
    current_stage: str
    overall_status: OverallStatus
    risk_level: RiskLevel
    feishu_chat_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WorkflowNodeRead(BaseModel):
    id: int
    case_id: int
    node_name: str
    owner_open_id: Optional[str]
    collaborator_open_id: Optional[str]
    due_date: date
    completed_at: Optional[datetime]
    status: NodeStatus
    overdue_days: int
    escalation_level: int

    model_config = ConfigDict(from_attributes=True)


class CaseProgress(BaseModel):
    case_id: int
    candidate_name: str
    current_stage: str
    overall_status: str
    risk_level: str
    nodes: list[WorkflowNodeRead]


class MaterialReviewRequest(BaseModel):
    material_type: str
    candidate_name: str
    expected_onboard_date: Optional[date] = None
    ocr_text: str = ""
    mock_extracted_fields: dict[str, Any] = Field(default_factory=dict)
    use_gemini_judgement: bool = True


class MaterialReviewFileRequest(BaseModel):
    material_type: str
    candidate_name: str
    file_path: str
    case_id: Optional[int] = None
    expected_onboard_date: Optional[date] = None
    file_token: Optional[str] = None
    file_name: Optional[str] = None
    use_gemini_judgement: bool = True


class MaterialCheck(BaseModel):
    rule: str
    status: Literal["pass", "fail", "manual_review"]
    reason: str


class MaterialReviewResult(BaseModel):
    material_type: str
    decision: Literal["auto_approved", "auto_rejected", "manual_review"]
    extracted_fields: dict[str, Any]
    checks: list[MaterialCheck]
    message_to_candidate: str
    hr_notes: Optional[str] = None
    review_source: str = "rules"


class MaterialReviewFileResponse(BaseModel):
    review: MaterialReviewResult
    submission_id: Optional[int] = None
    ocr_text: str
    quality_notes: list[str] = Field(default_factory=list)
    bitable_sync: dict[str, Any] = Field(default_factory=dict)
    candidate_notify: dict[str, Any] = Field(default_factory=dict)


class QARequest(BaseModel):
    question: str


class QASource(BaseModel):
    id: str
    title: str
    path: str


class QAResponse(BaseModel):
    answer: str
    sources: list[QASource]
    needs_handoff: bool = False
