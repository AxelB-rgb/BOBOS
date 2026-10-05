from __future__ import annotations
from pathlib import Path
import sqlite3
from fastapi import FastAPI, Query, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from .config import DB_PATH, DEFAULT_LIMIT, MIN_DURATION_HOURS, MIN_HVAC_KWH_PER_HOUR, MIN_LIGHTING_KWH_PER_HOUR
from .insights import detect_anomalies
from .dashboard import dashboard, metadata
from .smart import smart, apply_action, history, llm_status

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / 'static'
EXPORTS_DIR = BASE_DIR / 'exports'
app = FastAPI(title='Copilote des espaces', description='BIM × IoT · Agrégation SQL, IA décisionnelle et commandes simulées',version='1.0.0')


@app.exception_handler(ValueError)
async def invalid_input(request: Request, exc: ValueError):
    return JSONResponse(status_code=422,content={'detail':str(exc)})


@app.exception_handler(sqlite3.OperationalError)
async def database_error(request: Request, exc: sqlite3.OperationalError):
    return JSONResponse(status_code=503,content={'detail':'Base indisponible. Lancez python -m Backend.etl avant de démarrer le serveur.'})


def ready():
    if not DB_PATH.exists():
        raise HTTPException(503,'Base absente. Lancez python -m Backend.etl.')


def filters(
    date_from: str | None = Query(None,alias='from'),
    date_to: str | None = Query(None,alias='to'),
    min_hours: int = Query(MIN_DURATION_HOURS,ge=1,le=48),
    min_hvac_kwh: float = Query(MIN_HVAC_KWH_PER_HOUR,gt=0),
    min_lighting_kwh: float = Query(MIN_LIGHTING_KWH_PER_HOUR,gt=0),
    types: str | None = None,
    limit: int = Query(DEFAULT_LIMIT,ge=1,le=1000),
    business_hours_only: bool = False,
):
    ready()
    return dict(date_from=date_from,date_to=date_to,min_hours=min_hours,min_hvac_kwh=min_hvac_kwh,
                min_lighting_kwh=min_lighting_kwh,types=[t.strip() for t in types.split(',')] if types else None,
                limit=limit,business_hours_only=business_hours_only)


@app.get('/api/llm/status')
def provider_status():
    return llm_status()


@app.get('/api/health')
def health():
    return {'status':'ok','database_exists':DB_PATH.exists(),'milestones':[1,2,3]}


@app.get('/api/metadata',dependencies=[Depends(ready)])
def meta():
    return metadata()


@app.get('/api/dashboard',dependencies=[Depends(ready)])
def overview(date_from: str | None = Query(None,alias='from'),date_to: str | None = Query(None,alias='to')):
    return dashboard(date_from,date_to)


@app.get('/api/insights/raw')
def raw(options: dict = Depends(filters)):
    return detect_anomalies(**options)


@app.get('/api/insights/smart')
def recommendations(options: dict = Depends(filters), model: str | None = Query(None)):
    return smart(detect_anomalies(**options), model_override=model)


@app.post('/api/actions/{action_id}/apply',dependencies=[Depends(ready)])
def simulate(action_id: str):
    try:
        return apply_action(action_id)
    except KeyError:
        raise HTTPException(404,'Recommandation inconnue. Générez les cartes avant de les appliquer.')


@app.get('/api/actions/history',dependencies=[Depends(ready)])
def commands():
    return {'commands':history()}


app.mount('/static',StaticFiles(directory=STATIC_DIR),name='static')
if EXPORTS_DIR.exists():
    app.mount('/exports',StaticFiles(directory=EXPORTS_DIR),name='exports')


@app.get('/')
def index():
    return FileResponse(STATIC_DIR/'index.html')


@app.get('/map')
def map_view():
    return FileResponse(STATIC_DIR/'map.html')
