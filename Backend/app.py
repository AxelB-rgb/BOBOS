from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import (
    DB_PATH,
    DEFAULT_LIMIT,
    MIN_DURATION_HOURS,
    MIN_HVAC_KWH_PER_HOUR,
    MIN_LIGHTING_KWH_PER_HOUR,
)
from .insights import detect_anomalies

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="Copilote d'Optimisation des Espaces",
    description="Jalon 1 — agrégation BOS et API brute d'anomalies",
    version="0.1.0",
)


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "database": str(DB_PATH),
        "database_exists": DB_PATH.exists(),
        "jalon": 1,
    }


@app.get("/api/insights/raw")
def insights_raw(
    date_from: str | None = Query(default=None, alias="from", description="Début (YYYY-MM-DD)"),
    date_to: str | None = Query(default=None, alias="to", description="Fin (YYYY-MM-DD, incluse)"),
    min_hours: int = Query(default=MIN_DURATION_HOURS, ge=1, le=48),
    min_hvac_kwh: float = Query(default=MIN_HVAC_KWH_PER_HOUR, ge=0),
    min_lighting_kwh: float = Query(default=MIN_LIGHTING_KWH_PER_HOUR, ge=0),
    types: str | None = Query(
        default=None,
        description="Types séparés par des virgules: empty_room_hvac,empty_zone_hvac,empty_room_lighting",
    ),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=1000),
    business_hours_only: bool = Query(default=False),
) -> dict:
    parsed_types = [t.strip() for t in types.split(",")] if types else None
    return detect_anomalies(
        date_from=date_from,
        date_to=date_to,
        min_hours=min_hours,
        min_hvac_kwh=min_hvac_kwh,
        min_lighting_kwh=min_lighting_kwh,
        types=parsed_types,
        limit=limit,
        business_hours_only=business_hours_only,
    )


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"message": "GET /api/insights/raw"}
