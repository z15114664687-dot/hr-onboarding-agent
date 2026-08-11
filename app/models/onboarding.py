from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class CandidateCase(TimestampMixin, Base):
    __tablename__ = "candidate_cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    candidate_name: Mapped[str] = mapped_column(String(128), nullable=False)
    mobile: Mapped[Optional[str]] = mapped_column(String(64))
    email: Mapped[Optional[str]] = mapped_column(String(255))
    feishu_open_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    feishu_union_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    department: Mapped[Optional[str]] = mapped_column(String(128))
    position: Mapped[Optional[str]] = mapped_column(String(128))
    employment_type: Mapped[Optional[str]] = mapped_column(String(64))
    job_level: Mapped[Optional[str]] = mapped_column(String(64))
    city: Mapped[Optional[str]] = mapped_column(String(64))
    hrbp_open_id: Mapped[Optional[str]] = mapped_column(String(128))
    manager_open_id: Mapped[Optional[str]] = mapped_column(String(128))
    expected_onboard_date: Mapped[date] = mapped_column(Date, nullable=False)
    current_stage: Mapped[str] = mapped_column(String(64), default="medical_check", nullable=False)
    overall_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    risk_level: Mapped[str] = mapped_column(String(32), default="low", nullable=False)
    feishu_bitable_record_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    feishu_chat_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)

    nodes: Mapped[list["WorkflowNode"]] = relationship(
        back_populates="case",
        cascade="all, delete-orphan",
        order_by="WorkflowNode.due_date",
    )
    materials: Mapped[list["MaterialSubmission"]] = relationship(
        back_populates="case",
        cascade="all, delete-orphan",
    )


class WorkflowNode(TimestampMixin, Base):
    __tablename__ = "workflow_nodes"
    __table_args__ = (UniqueConstraint("case_id", "node_name", name="uq_workflow_node_case_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("candidate_cases.id"), nullable=False, index=True)
    node_name: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_open_id: Mapped[Optional[str]] = mapped_column(String(128))
    collaborator_open_id: Mapped[Optional[str]] = mapped_column(String(128))
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(32), default="not_started", nullable=False)
    overdue_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    escalation_level: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    feishu_bitable_record_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)

    case: Mapped[CandidateCase] = relationship(back_populates="nodes")


class MaterialSubmission(TimestampMixin, Base):
    __tablename__ = "material_submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("candidate_cases.id"), nullable=False, index=True)
    material_type: Mapped[str] = mapped_column(String(64), nullable=False)
    file_token: Mapped[Optional[str]] = mapped_column(String(255))
    file_name: Mapped[Optional[str]] = mapped_column(String(255))
    ocr_text: Mapped[Optional[str]] = mapped_column(Text)
    extracted_fields_json: Mapped[Optional[str]] = mapped_column(Text)
    review_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    review_reason: Mapped[Optional[str]] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    feishu_bitable_record_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)

    case: Mapped[CandidateCase] = relationship(back_populates="materials")


class ProcessEventLog(Base):
    __tablename__ = "process_event_logs"
    __table_args__ = (UniqueConstraint("external_event_id", name="uq_process_event_external_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    external_event_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    event_type: Mapped[Optional[str]] = mapped_column(String(128))
    payload_json: Mapped[Optional[str]] = mapped_column(Text)
    processed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
