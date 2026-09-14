-- Zone / étage vide (aucune pièce occupée) + circuit CVC (chauffage / froid / CTA).

WITH zone_hvac AS (
    SELECT
        hour,
        floor,
        zone,
        SUM(energy_kwh) AS hvac_kwh,
        GROUP_CONCAT(DISTINCT meters) AS meters
    FROM energy_hourly
    WHERE scope = 'zone'
      AND floor != ''
      AND zone != ''
      AND usage IN ('Heating', 'Cooling', 'Ventilation and Auxilaries')
      AND hour >= :date_from
      AND hour < :date_to
    GROUP BY hour, floor, zone
),
zone_occ AS (
    SELECT
        o.hour,
        r.floor,
        r.zone,
        MAX(o.occupied) AS occupied,
        SUM(o.n_samples) AS n_samples,
        COUNT(DISTINCT o.room) AS rooms_observed
    FROM occupancy_hourly o
    INNER JOIN rooms r ON r.code = o.room
    WHERE o.hour >= :date_from
      AND o.hour < :date_to
    GROUP BY o.hour, r.floor, r.zone
),
joined AS (
    SELECT
        h.hour,
        h.floor,
        h.zone,
        h.hvac_kwh,
        h.meters,
        z.occupied,
        z.n_samples,
        z.rooms_observed,
        CAST(strftime('%s', h.hour) AS INTEGER) / 3600 AS hour_index
    FROM zone_hvac h
    INNER JOIN zone_occ z
        ON z.hour = h.hour AND z.floor = h.floor AND z.zone = h.zone
    WHERE z.n_samples > 0
      AND z.occupied = 0
      AND h.hvac_kwh >= :min_hvac_kwh
),
islands AS (
    SELECT
        *,
        hour_index - ROW_NUMBER() OVER (PARTITION BY floor, zone ORDER BY hour) AS grp
    FROM joined
)
SELECT
    'empty_zone_hvac' AS type,
    NULL AS room,
    floor,
    zone,
    MIN(hour) AS start_at,
    MAX(hour) AS end_at,
    COUNT(*) AS duration_hours,
    ROUND(SUM(hvac_kwh), 3) AS wasted_kwh,
    MAX(meters) AS meters,
    SUM(n_samples) AS occupancy_samples
FROM islands
GROUP BY floor, zone, grp
HAVING COUNT(*) >= :min_hours
ORDER BY wasted_kwh DESC;
