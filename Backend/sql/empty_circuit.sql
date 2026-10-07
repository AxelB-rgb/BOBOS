-- One island per electrical meter, requiring EVERY explicitly served room observed empty.
WITH counts AS (
    SELECT circuit_id,COUNT(*) n_rooms FROM circuit_rooms GROUP BY circuit_id
), empty AS (
    SELECT cr.circuit_id,o.hour,SUM(o.n_samples) occupancy_samples
    FROM circuit_rooms cr JOIN occupancy_hourly o ON o.room=cr.room
    JOIN counts n ON n.circuit_id=cr.circuit_id
    WHERE o.hour>=:date_from AND o.hour<:date_to AND o.n_samples>0
    GROUP BY cr.circuit_id,o.hour
    HAVING COUNT(*)=MAX(n.n_rooms) AND MAX(o.occupied)=0
), active AS (
    SELECT m.sensor_id,m.circuit_id,m.label meters,m.usage,n.n_rooms,e.hour,e.energy_kwh,x.occupancy_samples,
        CASE WHEN m.usage='Lighting' THEN
            CASE WHEN n.n_rooms=1 THEN 'empty_room_lighting' ELSE 'empty_zone_lighting' END
        ELSE CASE WHEN n.n_rooms=1 THEN 'empty_room_hvac' ELSE 'empty_zone_hvac' END END type
    FROM electrical_hourly e JOIN electrical_meters m ON m.sensor_id=e.sensor_id
    JOIN empty x ON x.circuit_id=m.circuit_id AND x.hour=e.hour
    JOIN counts n ON n.circuit_id=m.circuit_id
    WHERE m.included=1 AND (
        (m.usage='Lighting' AND e.energy_kwh>=:min_lighting_kwh) OR
        (m.usage IN ('Heating','Cooling','Ventilation and Auxilaries') AND e.energy_kwh>=:min_hvac_kwh)
    )
), islands AS (
    SELECT *,CAST(strftime('%s',hour) AS INTEGER)/3600-
        ROW_NUMBER() OVER (PARTITION BY sensor_id ORDER BY hour) grp
    FROM active
)
SELECT type,sensor_id,circuit_id,MAX(meters) meters,MIN(hour) start_at,MAX(hour) end_at,
    COUNT(*) duration_hours,SUM(energy_kwh) wasted_kwh,SUM(occupancy_samples) occupancy_samples
FROM islands GROUP BY sensor_id,grp HAVING COUNT(*)>=:min_hours;
