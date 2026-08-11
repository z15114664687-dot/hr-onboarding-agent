import logging
import asyncio

from apscheduler.schedulers.background import BackgroundScheduler

from app.db.session import SessionLocal
from app.services.reminder_service import scan_overdue_and_notify

logger = logging.getLogger(__name__)


def create_scheduler() -> BackgroundScheduler:
    """Create APScheduler instance with daily overdue workflow scanning."""

    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(
        run_overdue_scan_job,
        "cron",
        hour=9,
        minute=0,
        id="daily_overdue_workflow_scan",
        replace_existing=True,
    )
    logger.info("reminder_scheduler_created")
    return scheduler


def run_overdue_scan_job() -> None:
    """Run the overdue scan in the scheduler thread."""

    try:
        result = asyncio.run(_run_overdue_scan())
        logger.info("reminder_overdue_scan_finished result=%s", result)
    except Exception:
        logger.exception("reminder_overdue_scan_failed")


async def _run_overdue_scan() -> dict[str, object]:
    db = SessionLocal()
    try:
        return await scan_overdue_and_notify(db)
    finally:
        db.close()
