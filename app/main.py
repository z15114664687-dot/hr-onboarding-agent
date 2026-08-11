from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI

from app.api.routes import admin, feishu_events, gemini_gateway, health, pages
from app.core.logging import setup_logging
from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.services.demo_seed import seed_demo_data
from app.services.reminder_scheduler import create_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    setup_logging()
    init_db()
    with SessionLocal() as db:
        seed_demo_data(db)
    scheduler = create_scheduler()
    scheduler.start()
    app.state.scheduler = scheduler
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(title="HR Onboarding Agent", version="0.1.0", lifespan=lifespan)
app.include_router(health.router)
app.include_router(feishu_events.router)
app.include_router(admin.router)
app.include_router(pages.router)
app.include_router(gemini_gateway.router)
