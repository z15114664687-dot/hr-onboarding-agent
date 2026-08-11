from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from app.api.routes import gemini_gateway
from app.core.logging import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    setup_logging()
    yield


app = FastAPI(title="HR Gemini Gateway", version="0.1.0", lifespan=lifespan)
app.include_router(gemini_gateway.router)


@app.get("/")
def root() -> dict[str, Any]:
    return {"status": "ok", "service": "hr-gemini-gateway"}
