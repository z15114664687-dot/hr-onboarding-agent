from app.models.base import Base
from app.models.onboarding import CandidateCase, MaterialSubmission, ProcessEventLog, WorkflowNode  # noqa: F401
from app.db.session import engine
from sqlalchemy import inspect, text


def init_db() -> None:
    """Create all local database tables."""

    Base.metadata.create_all(bind=engine)
    _ensure_sqlite_columns()


def _ensure_sqlite_columns() -> None:
    """Apply tiny local SQLite schema patches for MVP development."""

    if engine.dialect.name != "sqlite":
        return

    required_columns = {
        "candidate_cases": {
            "feishu_union_id": "VARCHAR(128)",
            "feishu_bitable_record_id": "VARCHAR(128)",
            "feishu_chat_id": "VARCHAR(128)",
            "job_level": "VARCHAR(64)",
        },
        "workflow_nodes": {"feishu_bitable_record_id": "VARCHAR(128)"},
        "material_submissions": {"feishu_bitable_record_id": "VARCHAR(128)"},
    }
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table_name, columns in required_columns.items():
            existing = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name, column_type in columns.items():
                if column_name not in existing:
                    conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"))
