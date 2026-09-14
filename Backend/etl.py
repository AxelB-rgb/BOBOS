"""Charge les CSV IoT + espaces BIM dans SQLite (agrégations horaires)."""

from __future__ import annotations

import functools
import re
import sqlite3
from pathlib import Path

import pandas as pd

print = functools.partial(print, flush=True)

from .config import DATA_DIR, DB_PATH, IFC_PATH
from .db import connect, init_db

ROOM_RE = re.compile(r"(\d{2}-\d{2}[aA]?-[NS])")
IFC_SPACE_RE = re.compile(
    r"IFCSPACE\('[^']*',#[0-9]+,'([^']*)',[^,]*,[^,]*,#[0-9]+,#[0-9]+,('(?:[^']*)'|\$)"
)

FLOOR_FROM_NAME = [
    (re.compile(r"\bN4\b|\bR\+4\b", re.I), "Etage 4"),
    (re.compile(r"\bN3\b|\bR\+3\b", re.I), "Etage 3"),
    (re.compile(r"\bN2\b|\bR\+2\b", re.I), "Etage 2"),
    (re.compile(r"\bN1\b|\bR\+1\b", re.I), "Etage 1"),
    (re.compile(r"\bN0\b|\bRDC\b", re.I), "RDC"),
    (re.compile(r"\bN5\b|Terrasse", re.I), "Terrasse"),
]

ZONE_FROM_NAME = [
    (re.compile(r"\bESEO\b", re.I), "ESEO"),
    (re.compile(r"\bESTP\b", re.I), "ESTP"),
    (re.compile(r"Communs?", re.I), "ESPACES COMMUNS"),
]

HVAC_USAGES = {"Heating", "Cooling", "Ventilation and Auxilaries"}
HVAC_NAME_RE = re.compile(
    r"Ventilo|CTA|ArmClim|CVC|CLIM|Extracteur|Circuit Chaud|Circuit Froid|"
    r"Energie chaud|Energie froid|insuflation|Adiabatique",
    re.I,
)
SKIP_NAME_RE = re.compile(
    r"Consommation G[eé]n[eé]rale|ASCENSEUR|BALLON|plomberie|Inutilis",
    re.I,
)
LIGHTING_NAME_RE = re.compile(r"ECLAIRAGE|eclairage|\xe9clairage", re.I)


def decode_ifc(text: str) -> str:
    return (
        text.replace(r"\X\E9", "é")
        .replace(r"\X\E8", "è")
        .replace(r"\X\EA", "ê")
        .replace(r"\X\E0", "à")
        .replace(r"\X\E2", "â")
        .replace(r"\X\F4", "ô")
        .replace(r"\X\E7", "ç")
        .replace(r"\X\E4", "ä")
        .replace(r"\X\FC", "ü")
    )


def parse_ifc_rooms(path: Path) -> list[tuple[str, str]]:
    if not path.exists():
        return []
    rooms = []
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if "IFCSPACE(" not in line:
                continue
            match = IFC_SPACE_RE.search(line)
            if not match:
                continue
            code = match.group(1).strip()
            raw_name = match.group(2)
            name = "" if raw_name == "$" else decode_ifc(raw_name.strip("'"))
            if code:
                rooms.append((code, name))
    return rooms


def infer_floor(display_name: str, floor: str | None) -> str | None:
    if isinstance(floor, str) and floor and ";" not in floor:
        return floor
    for regex, label in FLOOR_FROM_NAME:
        if regex.search(display_name or ""):
            return label
    return floor if isinstance(floor, str) else None


def infer_zone(display_name: str, zone: str | None) -> str | None:
    if isinstance(zone, str) and zone:
        if zone.upper() in {"ESEO", "ESTP"}:
            return zone.upper() if zone != "ESPACES COMMUNS" else zone
        return zone
    for regex, label in ZONE_FROM_NAME:
        if regex.search(display_name or ""):
            return label
    return None


def extract_room(display_name: str, room: str | None) -> str | None:
    if isinstance(room, str) and ROOM_RE.fullmatch(room.strip()):
        return room.strip()
    if isinstance(display_name, str):
        found = ROOM_RE.findall(display_name)
        if found:
            return found[0]
    return None


def occupancy_files() -> list[Path]:
    return sorted((DATA_DIR / "occupancy_csv").glob("occupancy_*.csv"))


def energy_files() -> list[Path]:
    return sorted((DATA_DIR / "energy_csv").glob("energy_*.csv"))


def load_rooms(conn: sqlite3.Connection) -> None:
    print("Chargement des pièces (occupation + IFC)...")
    bim = {code: name for code, name in parse_ifc_rooms(IFC_PATH)}
    frames = []
    for path in occupancy_files():
        df = pd.read_csv(path, sep=";", usecols=["room", "floor", "zone"])
        frames.append(df.dropna(subset=["room"]).drop_duplicates("room"))
    occ_rooms = pd.concat(frames, ignore_index=True).drop_duplicates("room")
    rows = []
    for rec in occ_rooms.itertuples(index=False):
        rows.append(
            (
                rec.room,
                bim.get(rec.room) or rec.room,
                rec.floor,
                rec.zone,
                "occupancy+ifc" if rec.room in bim else "occupancy",
            )
        )
    for code, name in bim.items():
        if code not in occ_rooms["room"].values:
            rows.append((code, name, None, None, "ifc"))
    conn.execute("DELETE FROM rooms")
    conn.executemany(
        "INSERT INTO rooms(code, name, floor, zone, source) VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    print(f"  {len(rows)} pièces")


def load_occupancy(conn: sqlite3.Connection) -> None:
    print("Agrégation horaire de l'occupation...")
    conn.execute("DELETE FROM occupancy_hourly")
    total = 0
    for path in occupancy_files():
        df = pd.read_csv(path, sep=";", usecols=["timestamp", "value", "room"])
        df = df.dropna(subset=["room", "timestamp"])
        df["hour"] = pd.to_datetime(df["timestamp"]).dt.floor("h").dt.strftime("%Y-%m-%d %H:%M:%S")
        df["occupied"] = (
            df["value"].astype(str).str.lower().isin(["true", "1", "1.0"]).astype(int)
        )
        hourly = (
            df.groupby(["room", "hour"], as_index=False)
            .agg(occupied=("occupied", "max"), n_samples=("occupied", "size"))
        )
        conn.executemany(
            "INSERT INTO occupancy_hourly(room, hour, occupied, n_samples) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(room, hour) DO UPDATE SET "
            "occupied = MAX(occupancy_hourly.occupied, excluded.occupied), "
            "n_samples = occupancy_hourly.n_samples + excluded.n_samples",
            list(hourly.itertuples(index=False, name=None)),
        )
        conn.commit()
        total += len(hourly)
        print(f"  {path.name}: {len(hourly)} heures-pièces")
    print(f"  total occupancy_hourly ~ {total}")


def scope_for(room: str | None, name: str) -> str | None:
    if room:
        return "room"
    if re.search(r"Circuit Chaud|Circuit Froid|CTA ", name or "", re.I):
        return "zone"
    if re.search(r"D[ée]part G[ée]n[ée]ral (CVC|Ventilo)|armoire climatisation", name or "", re.I):
        return "zone"
    return None


def load_energy(conn: sqlite3.Connection) -> None:
    print("Agrégation horaire HVAC / éclairage...")
    conn.execute("DELETE FROM energy_hourly")
    usecols = [
        "timestamp",
        "display name",
        "sensor id",
        "value",
        "room",
        "floor",
        "zone",
        "re2020Usage",
    ]
    for path in energy_files():
        parts = []
        for chunk in pd.read_csv(path, sep=";", usecols=usecols, chunksize=250_000):
            chunk["display name"] = chunk["display name"].astype(str)
            chunk["re2020Usage"] = chunk["re2020Usage"].fillna("").astype(str)
            name = chunk["display name"]
            usage = chunk["re2020Usage"]
            skip = name.str.contains(SKIP_NAME_RE, regex=True)
            hvac = (~skip) & (
                usage.isin(HVAC_USAGES) | name.str.contains(HVAC_NAME_RE, regex=True)
            )
            lighting = (~skip) & (
                (usage == "Lighting") | name.str.contains(LIGHTING_NAME_RE, regex=True)
            )
            keep = hvac | lighting
            if not keep.any():
                continue
            chunk = chunk.loc[keep].copy()
            hvac = hvac.loc[keep]
            lighting = lighting.loc[keep]
            name = chunk["display name"]
            usage = chunk["re2020Usage"]
            chunk["usage"] = usage
            froid = name.str.contains(r"froid|clim", case=False)
            chaud = name.str.contains(r"chaud|heating|cvc|ventilo", case=False)
            vent = name.str.contains(HVAC_NAME_RE, regex=True)
            chunk.loc[lighting & ~hvac, "usage"] = "Lighting"
            unset_hvac = hvac & ~chunk["usage"].isin(HVAC_USAGES)
            chunk.loc[unset_hvac & froid, "usage"] = "Cooling"
            chunk.loc[unset_hvac & chaud & ~froid, "usage"] = "Heating"
            chunk.loc[unset_hvac & vent & ~chunk["usage"].isin(HVAC_USAGES), "usage"] = (
                "Ventilation and Auxilaries"
            )
            room_ok = chunk["room"].astype(str).str.fullmatch(r"\d{2}-\d{2}[aA]?-[NS]", na=False)
            from_name = chunk["display name"].str.extract(ROOM_RE, expand=False)
            chunk["extracted_room"] = chunk["room"].where(room_ok).fillna(from_name)
            chunk["floor"] = [
                infer_floor(n, f) for n, f in zip(chunk["display name"], chunk["floor"])
            ]
            chunk["zone"] = [
                infer_zone(n, z) for n, z in zip(chunk["display name"], chunk["zone"])
            ]
            chunk["scope"] = [
                scope_for(room if isinstance(room, str) else None, n)
                for room, n in zip(chunk["extracted_room"], chunk["display name"])
            ]
            chunk = chunk[chunk["scope"].notna() & chunk["usage"].isin(
                list(HVAC_USAGES) + ["Lighting"]
            )]
            if chunk.empty:
                continue
            parts.append(
                chunk[
                    [
                        "timestamp",
                        "sensor id",
                        "value",
                        "extracted_room",
                        "floor",
                        "zone",
                        "usage",
                        "scope",
                        "display name",
                    ]
                ]
            )
        if not parts:
            print(f"  {path.name}: 0 lignes utiles")
            continue
        df = pd.concat(parts, ignore_index=True)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df = df.dropna(subset=["timestamp", "value"])
        df = df.sort_values(["sensor id", "timestamp"])
        df["delta"] = df.groupby("sensor id")["value"].diff().clip(lower=0).fillna(0)
        df["hour"] = df["timestamp"].dt.floor("h").dt.strftime("%Y-%m-%d %H:%M:%S")
        df["room"] = df["extracted_room"].fillna("")
        df["floor"] = df["floor"].fillna("")
        df["zone"] = df["zone"].fillna("")
        hourly = (
            df.groupby(
                ["hour", "scope", "room", "floor", "zone", "usage"],
                as_index=False,
            )
            .agg(energy_kwh=("delta", "sum"), meters=("display name", _join_meters))
        )
        conn.executemany(
            "INSERT INTO energy_hourly(hour, scope, room, floor, zone, usage, energy_kwh, meters) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(hour, scope, room, floor, zone, usage) DO UPDATE SET "
            "energy_kwh = energy_hourly.energy_kwh + excluded.energy_kwh",
            [
                (
                    r.hour,
                    r.scope,
                    r.room,
                    r.floor,
                    r.zone,
                    r.usage,
                    float(r.energy_kwh),
                    r.meters,
                )
                for r in hourly.itertuples(index=False)
            ],
        )
        conn.commit()
        print(f"  {path.name}: {len(hourly)} heures agrégées")


def _join_meters(series: pd.Series) -> str:
    names = sorted({str(x)[:80] for x in series.dropna().unique()})
    return " | ".join(names[:6])


def run() -> Path:
    if not DATA_DIR.exists():
        raise SystemExit(f"Dossier données introuvable: {DATA_DIR}")
    conn = connect(DB_PATH)
    try:
        init_db(conn)
        load_rooms(conn)
        load_occupancy(conn)
        load_energy(conn)
    finally:
        conn.close()
    print(f"Base écrite: {DB_PATH}")
    return DB_PATH


if __name__ == "__main__":
    run()
