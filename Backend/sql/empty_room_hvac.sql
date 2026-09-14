-- Îlots temporels : heures consécutives (hour_index - row_number).
-- Pièce vide (occupation observée = 0) + HVAC actif >= :min_hours.

WITH room_hvac AS (
    SELECT
        hour,
        room,
        SUM(energy_kwh) AS hvac_kwh,
        GROUP_CONCAT(DISTINCT meters) AS meters
    FROM energy_hourly
    WHERE scope = 'room'
      AND room != ''
      AND usage IN ('Heating', 'Cooling', 'Ventilation and Auxilaries')
      AND hour >= :date_from
      AND hour < :date_to
    GROUP BY hour, room
),
joined AS (
    SELECT
        h.hour,
        h.room,
        h.hvac_kwh,
        h.meters,
        o.occupied,
        o.n_samples,
        CAST(strftime('%s', h.hour) AS INTEGER) / 3600 AS hour_index
    FROM room_hvac h
    INNER JOIN occupancy_hourly o
        ON o.room = h.room AND o.hour = h.hour
    WHERE o.n_samples > 0
      AND o.occupied = 0
      AND h.hvac_kwh >= :min_hvac_kwh
),
islands AS (
    SELECT
        *,
        hour_index - ROW_NUMBER() OVER (PARTITION BY room ORDER BY hour) AS grp
    FROM joined
)
SELECT
    'empty_room_hvac' AS type,
    room AS room,
    NULL AS floor,
    NULL AS zone,
    MIN(hour) AS start_at,
    MAX(hour) AS end_at,
    COUNT(*) AS duration_hours,
    ROUND(SUM(hvac_kwh), 3) AS wasted_kwh,
    MAX(meters) AS meters,
    SUM(n_samples) AS occupancy_samples
FROM islands
GROUP BY room, grp
HAVING COUNT(*) >= :min_hours
ORDER BY wasted_kwh DESC;
