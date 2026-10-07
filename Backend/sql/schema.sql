CREATE TABLE IF NOT EXISTS rooms (
    code TEXT PRIMARY KEY,
    name TEXT,
    floor TEXT,
    zone TEXT,
    source TEXT,
    area_m2 REAL,
    capacity INTEGER,
    capacity_source TEXT
);

CREATE TABLE IF NOT EXISTS occupancy_hourly (
    room TEXT NOT NULL,
    hour TEXT NOT NULL,
    occupied INTEGER NOT NULL,
    n_samples INTEGER NOT NULL,
    PRIMARY KEY (room, hour)
);

CREATE TABLE IF NOT EXISTS energy_hourly (
    hour TEXT NOT NULL,
    scope TEXT NOT NULL,
    room TEXT NOT NULL DEFAULT '',
    floor TEXT NOT NULL DEFAULT '',
    zone TEXT NOT NULL DEFAULT '',
    usage TEXT NOT NULL,
    energy_kwh REAL NOT NULL,
    meters TEXT,
    PRIMARY KEY (hour, scope, room, floor, zone, usage)
);

CREATE INDEX IF NOT EXISTS idx_occ_hour ON occupancy_hourly(hour);
CREATE INDEX IF NOT EXISTS idx_occ_room ON occupancy_hourly(room);
CREATE INDEX IF NOT EXISTS idx_energy_hour ON energy_hourly(hour);
CREATE INDEX IF NOT EXISTS idx_energy_room ON energy_hourly(room);
CREATE INDEX IF NOT EXISTS idx_energy_scope ON energy_hourly(scope, floor, zone);


-- Explicit MSI/CDE electrical topology; hosting room is never the served room.
CREATE TABLE IF NOT EXISTS electrical_circuits (
    id TEXT PRIMARY KEY, label TEXT NOT NULL, usage TEXT NOT NULL,
    panel TEXT, hosted_room TEXT, metadata TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS circuit_rooms (
    circuit_id TEXT NOT NULL, room TEXT NOT NULL, provenance TEXT NOT NULL,
    PRIMARY KEY(circuit_id, room)
);
CREATE TABLE IF NOT EXISTS circuit_assets (
    circuit_id TEXT NOT NULL, asset_id TEXT NOT NULL, payload TEXT NOT NULL,
    PRIMARY KEY(circuit_id, asset_id)
);
CREATE TABLE IF NOT EXISTS meter_hierarchy (
    parent TEXT NOT NULL, child TEXT NOT NULL, PRIMARY KEY(parent,child)
);
CREATE TABLE IF NOT EXISTS electrical_meters (
    sensor_id TEXT PRIMARY KEY, circuit_id TEXT NOT NULL, label TEXT NOT NULL,
    usage TEXT NOT NULL, included INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS electrical_hourly (
    sensor_id TEXT NOT NULL, hour TEXT NOT NULL, energy_kwh REAL NOT NULL,
    n_samples INTEGER NOT NULL, resets INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(sensor_id,hour)
);
CREATE INDEX IF NOT EXISTS idx_electrical_hour ON electrical_hourly(hour);
CREATE INDEX IF NOT EXISTS idx_circuit_rooms_room ON circuit_rooms(room);
CREATE TABLE IF NOT EXISTS electrical_import (key TEXT PRIMARY KEY, value TEXT NOT NULL);
