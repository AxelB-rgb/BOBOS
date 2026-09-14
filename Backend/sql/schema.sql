CREATE TABLE IF NOT EXISTS rooms (
    code TEXT PRIMARY KEY,
    name TEXT,
    floor TEXT,
    zone TEXT,
    source TEXT
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
