"""Détection d'anomalies SQL pour GET /api/insights/raw."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from .config import (
    DEFAULT_LIMIT,
    MIN_DURATION_HOURS,
    MIN_HVAC_KWH_PER_HOUR,
    MIN_LIGHTING_KWH_PER_HOUR,
)
from .db import connect

SQL_DIR = Path(__file__).resolve().parent / "sql"


def _load_sql(name: str) -> str:
    return (SQL_DIR / name).read_text(encoding="utf-8")


def _parse_dt(value: str | None, fallback: datetime) -> datetime:
    if not value:
        return fallback
    text = value.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text, fmt)
            if fmt == "%Y-%m-%d":
                return parsed
            return parsed
        except ValueError:
            continue
    return fallback


def _dataset_bounds(conn) -> tuple[datetime, datetime]:
    row = conn.execute(
        "SELECT MIN(hour) AS mn, MAX(hour) AS mx FROM occupancy_hourly"
    ).fetchone()
    if not row or not row["mn"]:
        now = datetime(2026, 1, 12)
        return now, now + timedelta(days=1)
    start = datetime.fromisoformat(row["mn"])
    end = datetime.fromisoformat(row["mx"]) + timedelta(hours=1)
    return start, end


def _enrich(row: dict, rooms: dict[str, dict]) -> dict:
    room_code = row.get("room") or None
    meta = rooms.get(room_code or "", {})
    floor = row.get("floor") or meta.get("floor")
    zone = row.get("zone") or meta.get("zone")
    name = meta.get("name") or room_code
    start = datetime.fromisoformat(row["start_at"])
    last_hour = datetime.fromisoformat(row["end_at"])
    end = last_hour + timedelta(hours=1)
    duration = int(row["duration_hours"])
    wasted = float(row["wasted_kwh"] or 0)
    kind = row["type"]

    if duration >= 8 or wasted >= 20:
        severity = "high"
    elif duration >= 4 or wasted >= 5:
        severity = "medium"
    else:
        severity = "low"

    business_hours = 0
    cursor = start
    while cursor < end:
        if cursor.weekday() < 5 and 8 <= cursor.hour < 18:
            business_hours += 1
        cursor += timedelta(hours=1)

    if kind == "empty_room_hvac":
        evidence = (
            f"La pièce {name} ({room_code}) est inoccupée pendant {duration} h "
            f"alors que le HVAC consomme {wasted:.2f} kWh "
            f"({row.get('meters') or 'compteur CVC'})."
        )
    elif kind == "empty_zone_hvac":
        evidence = (
            f"Toutes les pièces observées de {zone} / {floor} sont vides pendant "
            f"{duration} h alors que le CVC de zone consomme {wasted:.2f} kWh."
        )
    else:
        evidence = (
            f"La pièce {name} ({room_code}) est inoccupée pendant {duration} h "
            f"alors que l'éclairage consomme {wasted:.2f} kWh."
        )

    return {
        "id": f"{kind}:{room_code or zone}:{start.isoformat()}",
        "type": kind,
        "severity": severity,
        "room": room_code,
        "room_name": name if room_code else None,
        "floor": floor,
        "zone": zone,
        "start_at": start.isoformat(sep=" "),
        "end_at": end.isoformat(sep=" "),
        "duration_hours": duration,
        "business_hours": business_hours,
        "occupied": False,
        "energy_kwh": round(wasted, 3),
        "meters": [m.strip() for m in (row.get("meters") or "").split("|") if m.strip()],
        "occupancy_samples": int(row.get("occupancy_samples") or 0),
        "evidence": evidence,
    }


def _balanced_sample(anomalies: list[dict], limit: int, wanted: set[str]) -> list[dict]:
    """Garde les plus gros gaspillages tout en mélangeant les types d'anomalies."""
    if len(anomalies) <= limit:
        return anomalies
    buckets: dict[str, list[dict]] = {kind: [] for kind in wanted}
    for item in anomalies:
        buckets.setdefault(item["type"], []).append(item)
    kinds = [k for k in wanted if buckets.get(k)]
    quota = max(1, limit // max(len(kinds), 1))
    picked: list[dict] = []
    seen: set[str] = set()
    for kind in kinds:
        for item in buckets[kind][:quota]:
            if item["id"] not in seen:
                picked.append(item)
                seen.add(item["id"])
    for item in anomalies:
        if len(picked) >= limit:
            break
        if item["id"] not in seen:
            picked.append(item)
            seen.add(item["id"])
    picked.sort(key=lambda a: a["energy_kwh"], reverse=True)
    return picked[:limit]


def detect_anomalies(
    date_from: str | None = None,
    date_to: str | None = None,
    min_hours: int = MIN_DURATION_HOURS,
    min_hvac_kwh: float = MIN_HVAC_KWH_PER_HOUR,
    min_lighting_kwh: float = MIN_LIGHTING_KWH_PER_HOUR,
    types: list[str] | None = None,
    limit: int = DEFAULT_LIMIT,
    business_hours_only: bool = False,
) -> dict:
    conn = connect()
    try:
        data_from, data_to = _dataset_bounds(conn)
        start = _parse_dt(date_from, data_from)
        end = _parse_dt(date_to, data_to)
        if date_to and len(date_to.strip()) == 10:
            end = end + timedelta(days=1)
        if end <= start:
            end = start + timedelta(days=1)

        params = {
            "date_from": start.strftime("%Y-%m-%d %H:%M:%S"),
            "date_to": end.strftime("%Y-%m-%d %H:%M:%S"),
            "min_hours": int(min_hours),
            "min_hvac_kwh": float(min_hvac_kwh),
            "min_lighting_kwh": float(min_lighting_kwh),
        }

        wanted = set(types or ["empty_room_hvac", "empty_zone_hvac", "empty_room_lighting"])
        queries = []
        if "empty_room_hvac" in wanted:
            queries.append(_load_sql("empty_room_hvac.sql"))
        if "empty_zone_hvac" in wanted:
            queries.append(_load_sql("empty_zone_hvac.sql"))
        if "empty_room_lighting" in wanted:
            queries.append(_load_sql("empty_room_lighting.sql"))

        rooms = {
            r["code"]: dict(r)
            for r in conn.execute("SELECT code, name, floor, zone FROM rooms").fetchall()
        }

        raw_rows = []
        for sql in queries:
            raw_rows.extend(dict(r) for r in conn.execute(sql, params).fetchall())
    finally:
        conn.close()

    anomalies = [_enrich(row, rooms) for row in raw_rows]
    if business_hours_only:
        anomalies = [a for a in anomalies if a["business_hours"] >= min_hours]
    anomalies.sort(key=lambda a: a["energy_kwh"], reverse=True)

    cap = max(1, min(limit, 1000))
    truncated = _balanced_sample(anomalies, cap, wanted)

    by_type: dict[str, int] = {}
    for item in truncated:
        by_type[item["type"]] = by_type.get(item["type"], 0) + 1

    rooms_affected = {a["room"] for a in truncated if a["room"]}
    zones_affected = {
        f"{a['zone']}/{a['floor']}" for a in truncated if a["zone"] and not a["room"]
    }
    return {
        "generated_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
        "period": {
            "from": start.isoformat(sep=" "),
            "to": end.isoformat(sep=" "),
        },
        "thresholds": {
            "min_duration_hours": int(min_hours),
            "min_hvac_kwh_per_hour": float(min_hvac_kwh),
            "min_lighting_kwh_per_hour": float(min_lighting_kwh),
            "business_hours_only": business_hours_only,
        },
        "summary": {
            "anomaly_count": len(truncated),
            "total_wasted_kwh": round(sum(a["energy_kwh"] for a in truncated), 3),
            "rooms_affected": len(rooms_affected),
            "zones_affected": len(zones_affected),
            "by_type": by_type,
        },
        "anomalies": truncated,
    }
