-- A single measured presence is enough; missing hours and separate nights break episodes.
WITH observed AS (
 SELECT room,hour,n_samples,
 CAST(strftime('%s',hour) AS INTEGER)/3600 - ROW_NUMBER() OVER (PARTITION BY room ORDER BY hour) grp
 FROM occupancy_hourly
 WHERE hour>=:date_from AND hour<:date_to AND occupied=1 AND n_samples>0
 AND room IN (SELECT code FROM rooms) AND datetime(hour) IS NOT NULL
 AND ((:night_start<:night_end AND CAST(strftime('%H',hour) AS INTEGER)>=:night_start AND CAST(strftime('%H',hour) AS INTEGER)<:night_end)
 OR (:night_start>:night_end AND (CAST(strftime('%H',hour) AS INTEGER)>=:night_start OR CAST(strftime('%H',hour) AS INTEGER)<:night_end)))
 AND NOT EXISTS (SELECT 1 FROM json_each(:exceptions) exception
 WHERE json_extract(exception.value,'$.room') IN ('*',occupancy_hourly.room)
 AND json_extract(exception.value,'$.start')<datetime(occupancy_hourly.hour,'+1 hour')
 AND json_extract(exception.value,'$.end')>occupancy_hourly.hour)
)
SELECT 'night_presence' type,room,MIN(hour) start_at,MAX(hour) end_at,
 COUNT(*) duration_hours,0 wasted_kwh,SUM(n_samples) occupancy_samples,'' meters
FROM observed GROUP BY room,grp;
