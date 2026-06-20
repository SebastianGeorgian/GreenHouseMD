-- ─────────────────────────────────────────────
-- Greenhouse Monitor — database schema
-- Run:  psql -U pi_user -d greenhouse -f schema.sql
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS sensor_readings (
    id          SERIAL PRIMARY KEY,
    sensor_type VARCHAR(50)      NOT NULL,
    value       DOUBLE PRECISION NOT NULL,
    timestamp   TIMESTAMP        NOT NULL DEFAULT NOW()
);

-- Composite index: all application queries filter on
-- sensor_type + timestamp, so this index significantly
-- speeds up the dashboard as data accumulates.
CREATE INDEX IF NOT EXISTS idx_sensor_readings_type_ts
    ON sensor_readings (sensor_type, timestamp DESC);
