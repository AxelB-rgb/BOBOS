-- Éclairage consommé dans une pièce observée vide.

WITH room_light AS (
    SELECT
        hour,
        room,
        SUM(energy_kwh) AS lighting_kwh,
        GROUP_CONCAT(DISTINCT meters) AS meters
    FROM energy_hourly
    WHERE scope = 'room'
      AND room != ''
      AND usage = 'Lighting'
      AND hour >= :date_from
      AND hour < :date_to
    GROUP BY hour, room
),
joined AS (
    SELECT
        l.hour,
        l.room,
        l.lighting_kwh,
        l.meters,
        o.occupied,
        o.n_samples,
        CAST(strftime('%s', l.hour) AS INTEGER) / 3600 AS hour_index
    FROM room_light l
    INNER JOIN occupancy_hourly o
        ON o.room = l.room AND o.hour = l.hour
    WHERE o.n_samples > 0
      AND o.occupied = 0
      AND l.lighting_kwh >= :min_lighting_kwh
),
islands AS (
    SELECT
        *,
        hour_index - ROW_NUMBER() OVER (PARTITION BY room ORDER BY hour) AS grp
    FROM joined
)
SELECT
    'empty_room_lighting' AS type,
    room,
    NULL AS floor,
    NULL AS zone,
    MIN(hour) AS start_at,
    MAX(hour) AS end_at,
    COUNT(*) AS duration_hours,
    ROUND(SUM(lighting_kwh), 3) AS wasted_kwh,
    MAX(meters) AS meters,
    SUM(n_samples) AS occupancy_samples
FROM islands
GROUP BY room, grp
HAVING COUNT(*) >= :min_hours
ORDER BY wasted_kwh DESC;
