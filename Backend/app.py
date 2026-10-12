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

STATIC_DIR = Path(__file__).resolve().parent / 'static'
EXPORTS_DIR = Path(__file__).resolve().parent.parent / 'exports'
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


@app.get('/api/energy/circuits',dependencies=[Depends(ready)])
def electrical_circuits(date_from: str | None = Query(None,alias='from'),date_to: str | None = Query(None,alias='to')):
    from .dashboard import period
    from .electrical import circuits_report
    from .db import connect
    with connect() as conn:
        start,end = period(conn,date_from,date_to)
        return circuits_report(conn,start,end)


@app.get('/api/map/data',dependencies=[Depends(ready)])
def map_data(date_from: str | None = Query(None,alias='from'),date_to: str | None = Query(None,alias='to')):
    """Map read model: includes wired circuits with no measured counter."""
    from .db import connect
    result = dashboard(date_from,date_to)
    if result['electrical']['available']:
        with connect() as conn:
            for row in conn.execute("SELECT c.* FROM electrical_circuits c WHERE NOT EXISTS(SELECT 1 FROM electrical_meters m WHERE m.circuit_id=c.id)"):
                rooms = [dict(r) for r in conn.execute('SELECT r.code,r.name,r.floor FROM circuit_rooms cr JOIN rooms r ON r.code=cr.room WHERE cr.circuit_id=?',(row['id'],))]
                result['electrical']['circuits'].append(dict(circuit_id=row['id'],label=row['label'],usage=row['usage'],rooms=rooms,energy_kwh=None,included=False,mapping='shared' if len(rooms)>1 else 'dedicated' if rooms else 'unmapped',unmeasured=True))
    if result['electrical']['available']:
        with connect() as conn:
            conflicts={r[0]:r[1] for r in conn.execute("SELECT circuit_id,COUNT(*) FROM circuit_assets WHERE json_extract(payload,'$.localisation_conflict')=1 OR json_extract(payload,'$.cde_match_status')='ambiguous' GROUP BY circuit_id")}
        for circuit in result['electrical']['circuits']:
            circuit['mapping_conflicts']=conflicts.get(circuit['circuit_id'],0)
    return result


@app.get('/api/energy/circuits/{circuit_id}/assets',dependencies=[Depends(ready)])
def circuit_assets(circuit_id: str):
    import json
    from .db import connect
    with connect() as conn:
        if not conn.execute('SELECT 1 FROM electrical_circuits WHERE id=?',(circuit_id,)).fetchone():
            raise HTTPException(404,'Départ inconnu.')
        assets = [json.loads(r[0]) for r in conn.execute('SELECT payload FROM circuit_assets WHERE circuit_id=? ORDER BY asset_id',(circuit_id,))]
    groups = {}
    for asset in assets:
        matches = asset.get('cde_matches',[])
        label = matches[0].get('name') if len(matches)==1 else asset.get('name')
        key = (label,asset.get('hosted_room'))
        if key not in groups:
            groups[key] = {'name':label,'room':asset.get('hosted_room'),'id':asset.get('id'),'count':0}
        groups[key]['count'] += 1
    return {'assets':list(groups.values()),'total':len(assets)}


@app.get('/api/insights/raw')
def raw(options: dict = Depends(filters)):
    return detect_anomalies(**options)


@app.get('/api/insights/smart')
def recommendations(options: dict = Depends(filters), model: str | None = Query(None), preview: bool = False):
    from .rules import business_alerts,recommendation_input
    raw=detect_anomalies(**options)
    return smart(recommendation_input(raw,business_alerts(raw)),model_override=model,use_llm=not preview)


@app.post('/api/actions/{action_id}/apply',dependencies=[Depends(ready)])
def simulate(action_id: str):
    try:
        return apply_action(action_id)
    except KeyError:
        raise HTTPException(404,'Recommandation inconnue. Générez les cartes avant de les appliquer.')


@app.get('/api/actions/history',dependencies=[Depends(ready)])
def commands():
    from .journal import entries
    return {'commands':entries()}


from .journal import JournalEntry

@app.post('/api/actions/history',dependencies=[Depends(ready)],status_code=201)
def add_journal_entry(value: JournalEntry):
    from .journal import add
    return add(value)


@app.delete('/api/actions/history/{entry_id}',dependencies=[Depends(ready)])
def remove_journal_entry(entry_id: str):
    from .journal import remove
    try:return remove(entry_id)
    except KeyError:raise HTTPException(404,'Action du journal inconnue.')


@app.post('/api/actions/history/{entry_id}/restore',dependencies=[Depends(ready)])
def restore_journal_entry(entry_id: str):
    from .journal import remove
    try:return remove(entry_id,restore=True)
    except KeyError:raise HTTPException(404,'Action du journal inconnue.')


from .rules import RuleSettings, Decision, settings, save_settings, business_alerts, qualify, decision_history, catalogue

@app.get('/api/rules',dependencies=[Depends(ready)])
def rule_configuration():
    value=settings()
    return {'settings':value.model_dump(mode='json'),'catalogue':catalogue(value),'max_period_days':31,
            'access':'Démonstrateur local sans comptes ; tous les utilisateurs locaux peuvent qualifier les alertes et régler les règles.'}

@app.put('/api/rules',dependencies=[Depends(ready)])
def update_rule_configuration(value: RuleSettings):
    return save_settings(value)

@app.get('/api/alerts')
def alerts(options: dict = Depends(filters)):
    options['limit']=1000
    return business_alerts(detect_anomalies(**options))

@app.get('/api/alerts/history',dependencies=[Depends(ready)])
def alert_history():
    return {'decisions':decision_history()}

@app.get('/api/alerts/{alert_id}',dependencies=[Depends(ready)])
def alert_detail(alert_id: str):
    import json
    from .db import connect
    with connect() as conn:
        from .rules import ensure_tables
        ensure_tables(conn)
        row=conn.execute('SELECT payload FROM alert_registry WHERE id=?',(alert_id,)).fetchone()
        if not row:raise HTTPException(404,'Alerte inconnue. Actualisez le bilan.')
        return json.loads(row[0])

@app.post('/api/alerts/{alert_id}/decision',dependencies=[Depends(ready)])
def alert_decision(alert_id: str, value: Decision):
    try:return qualify(alert_id,value)
    except KeyError:raise HTTPException(404,'Alerte inconnue. Actualisez le bilan.')

app.mount('/static',StaticFiles(directory=STATIC_DIR),name='static')
app.mount('/exports',StaticFiles(directory=EXPORTS_DIR),name='exports')


@app.get('/')
def index():
    return FileResponse(STATIC_DIR/'index.html')


@app.get('/map')
def map_view():
    return FileResponse(STATIC_DIR/'map.html')
