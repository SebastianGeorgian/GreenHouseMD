-- ─────────────────────────────────────────────
-- Greenhouse Monitor — schema baza de date
-- Ruleaza:  psql -U pi_sebastian -d greenhouse -f schema.sql
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS sensor_readings (
    id          SERIAL PRIMARY KEY,
    sensor_type VARCHAR(50)      NOT NULL,
    value       DOUBLE PRECISION NOT NULL,
    timestamp   TIMESTAMP        NOT NULL DEFAULT NOW()
);

-- Index compus: toate query-urile aplicatiei filtreaza pe
-- sensor_type + timestamp, deci acest index accelereaza
-- dashboardul semnificativ pe masura ce datele se aduna.
CREATE INDEX IF NOT EXISTS idx_sensor_readings_type_ts
    ON sensor_readings (sensor_type, timestamp DESC);
