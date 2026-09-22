"""FastAPI application. OpenAPI is served at /openapi.json, docs at /docs."""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .. import db
from ..config import settings
from . import afdd_routes, agent_routes, platform_routes

logging.basicConfig(
    level=settings.log_level,
    format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
)
log = logging.getLogger("afdd.api")

DESCRIPTION = """
Multi-site Automatic Fault Detection and Diagnostics over a Brickschema model.

* **Foundation** - telemetry ingestion with ontology-resolved identity, TimescaleDB
  history, guarded current values, and visible rejects, duplicates and gaps.
* **AFDD engine** - versioned rules whose target scope and fault logic are separate
  configuration, evaluated deterministically on device observation time.
* **Operations** - portfolio health, issue evidence, affected-space reasoning.
* **AI rule authoring** - a bounded agent that drafts rules for human confirmation.
  It can never activate anything.
"""

app = FastAPI(
    title="AltoTech multi-site AFDD platform",
    version="1.0.0",
    description=DESCRIPTION,
    openapi_tags=[
        {"name": "health", "description": "Liveness, pipeline health and data quality"},
        {"name": "ontology", "description": "Brickschema entities, relationships and spatial context"},
        {"name": "telemetry", "description": "Current values and history"},
        {"name": "portfolio", "description": "Property overview"},
        {"name": "rules", "description": "Rule versions, target preview, activation"},
        {"name": "issues", "description": "Detected issues and their evidence"},
        {"name": "backtest", "description": "Historical replay, isolated from live"},
        {"name": "agent", "description": "AI-assisted rule authoring"},
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.api_cors_origins.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(platform_routes.router, prefix="/api")
app.include_router(afdd_routes.router, prefix="/api")
app.include_router(agent_routes.router, prefix="/api")


@app.middleware("http")
async def structured_logging(request: Request, call_next):
    request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
    started = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:  # noqa: BLE001
        log.exception("request failed path=%s request_id=%s", request.url.path, request_id)
        return JSONResponse({"detail": "internal error", "request_id": request_id}, status_code=500)
    duration = (time.monotonic() - started) * 1000
    response.headers["x-request-id"] = request_id
    log.info(
        "%s %s -> %s in %.1fms request_id=%s",
        request.method, request.url.path, response.status_code, duration, request_id,
    )
    return response


@app.on_event("startup")
def _startup() -> None:
    db.wait_for_db()
    log.info("api ready")


@app.on_event("shutdown")
def _shutdown() -> None:
    db.close_pool()


@app.get("/", include_in_schema=False)
def root() -> dict:
    return {"service": "afdd-platform", "docs": "/docs", "openapi": "/openapi.json"}
