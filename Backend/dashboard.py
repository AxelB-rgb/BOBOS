"""Read models for the Facility Manager dashboard."""
from datetime import datetime, timedelta
from .db import connect
from .config import AREA_PER_PERSON, ENERGY_PRICE
from .insights import _parse_dt, _dataset_bounds


def period(conn, date_from=None, date_to=None):
    lo, hi = _dataset_bounds(conn)
    start = _parse_dt(date_from, lo)
    end = _parse_dt(date_to, hi)
    if date_to and len(date_to) == 10:
        end += timedelta(days=1)
    if end <= start:
        raise ValueError('Période invalide.')
    return start.strftime('%Y-%m-%d %H:%M:%S'), end.strftime('%Y-%m-%d %H:%M:%S')


def metadata():
    with connect() as conn:
        lo, hi = _dataset_bounds(conn)
        return {'from': lo.date().isoformat(), 'to': (hi-timedelta(hours=1)).date().isoformat(),
                'default_date': min(lo+timedelta(days=11), hi-timedelta(hours=1)).date().isoformat(),
                'rooms': conn.execute('SELECT COUNT(*) FROM rooms').fetchone()[0],
                'monitored_rooms': conn.execute('SELECT COUNT(DISTINCT room) FROM occupancy_hourly').fetchone()[0],
                'price_eur_kwh': ENERGY_PRICE, 'area_per_person_m2': AREA_PER_PERSON,
                'limitations': ['Présence binaire : utilisation horaire, pas un comptage de personnes.',
                               'Capacité estimée à partir de la surface IFC ; non réglementaire.',
                               'Pas de données de réservation. Économies indicatives ; compteurs potentiellement imbriqués.']}


def dashboard(date_from=None, date_to=None):
    with connect() as conn:
        start, end = period(conn, date_from, date_to)
        params = {'start': start, 'end': end}
        rooms = [dict(r) for r in conn.execute('''
            WITH occ AS (
                SELECT room, COUNT(*) observed_hours, SUM(occupied) occupied_hours,
                    SUM(CASE WHEN CAST(strftime('%H',hour) AS INT) BETWEEN 8 AND 17
                        AND strftime('%w',hour) NOT IN ('0','6') THEN 1 ELSE 0 END) business_observed,
                    SUM(CASE WHEN CAST(strftime('%H',hour) AS INT) BETWEEN 8 AND 17
                        AND strftime('%w',hour) NOT IN ('0','6') THEN occupied ELSE 0 END) business_occupied
                FROM occupancy_hourly WHERE hour >= :start AND hour < :end GROUP BY room
            ), energy AS (
                SELECT room, SUM(energy_kwh) energy_kwh FROM energy_hourly
                WHERE scope='room' AND hour >= :start AND hour < :end GROUP BY room
            )
            SELECT r.*, o.observed_hours, o.occupied_hours, o.business_observed, o.business_occupied,
                e.energy_kwh FROM rooms r LEFT JOIN occ o ON r.code=o.room
                LEFT JOIN energy e ON r.code=e.room ORDER BY r.floor,r.code
        ''', params)]
        for r in rooms:
            r['utilization_pct'] = round(100*r['business_occupied']/r['business_observed'],1) if r['business_observed'] else None
            r['coverage_pct'] = round(100*(r['observed_hours'] or 0)/((datetime.fromisoformat(end)-datetime.fromisoformat(start)).total_seconds()/3600),1)
            r['energy_kwh'] = round(r['energy_kwh'],2) if r['energy_kwh'] is not None else None
        trend = [dict(r) for r in conn.execute('''
            WITH e AS (SELECT hour,SUM(energy_kwh) energy_kwh FROM energy_hourly
                       WHERE hour>=:start AND hour<:end GROUP BY hour),
            o AS (SELECT hour,AVG(occupied)*100 utilization_pct,COUNT(*) rooms_observed
                  FROM occupancy_hourly WHERE hour>=:start AND hour<:end GROUP BY hour)
            SELECT o.hour,o.utilization_pct,o.rooms_observed,e.energy_kwh FROM o LEFT JOIN e ON o.hour=e.hour ORDER BY o.hour
        ''', params)]
        floors = {}
        for r in rooms:
            if not r['observed_hours']:
                continue
            f = floors.setdefault(r['floor'] or 'Non renseigné', {'floor':r['floor'] or 'Non renseigné','rooms':0,'observed':0,'occupied':0,'capacity':0})
            f['rooms'] += 1
            f['observed'] += r['business_observed'] or 0
            f['occupied'] += r['business_occupied'] or 0
            f['capacity'] += r['capacity'] or 0
        for f in floors.values():
            f['utilization_pct'] = round(100*f['occupied']/f['observed'],1) if f['observed'] else None
        observed = sum(r['business_observed'] or 0 for r in rooms)
        occupied = sum(r['business_occupied'] or 0 for r in rooms)
        return {'period':{'from':start,'to':end}, 'rooms':rooms,'floors':list(floors.values()), 'trend':trend,
                'summary':{'utilization_pct':round(100*occupied/observed,1) if observed else None,
                           'monitored_rooms':sum(bool(r['observed_hours']) for r in rooms),
                           'energy_kwh':round(sum(t['energy_kwh'] or 0 for t in trend),2),
                           'estimated_capacity':sum(r['capacity'] or 0 for r in rooms)}}
