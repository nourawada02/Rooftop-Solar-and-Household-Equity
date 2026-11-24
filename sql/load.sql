-- load.sql
-- Loads the cleaned CSV files into the tables created by schema.sql.
-- Inside the Docker container the cleaned files are mounted at /data/clean.
--
-- Run from the project root (PowerShell):
--   Get-Content sql/schema.sql | docker compose exec -T postgres psql -U solar -d solar_equity
--   Get-Content sql/load.sql   | docker compose exec -T postgres psql -U solar -d solar_equity
--
-- The script can be re-run: it empties the tables first. Everything happens in one
-- transaction, so a failure leaves the database unchanged.

\set ON_ERROR_STOP on
BEGIN;

TRUNCATE reading_hourly, household, acorn_group, acorn_category, tariff,
         weather_hourly, calendar_month RESTART IDENTITY CASCADE;

-- 1. Lookup tables with fixed content -------------------------------------------------
INSERT INTO tariff (tariff_code, description) VALUES
    ('Std', 'Standard flat-rate tariff'),
    ('ToU', 'Dynamic time-of-use tariff');

INSERT INTO calendar_month (month_no, month_name, season) VALUES
    (1, 'January', 'Winter'),  (2, 'February', 'Winter'),  (3, 'March', 'Spring'),
    (4, 'April', 'Spring'),    (5, 'May', 'Spring'),       (6, 'June', 'Summer'),
    (7, 'July', 'Summer'),     (8, 'August', 'Summer'),    (9, 'September', 'Autumn'),
    (10, 'October', 'Autumn'), (11, 'November', 'Autumn'), (12, 'December', 'Winter');

-- 2. Households: the CSV is one flat table, so it is split into three tables ----------
CREATE TEMP TABLE staging_household (
    household_id   TEXT,
    tariff         TEXT,
    acorn_code     TEXT,
    acorn_category TEXT
) ON COMMIT DROP;

COPY staging_household FROM '/data/clean/households_clean.csv' WITH (FORMAT csv, HEADER true);

INSERT INTO acorn_category (category_name)
SELECT DISTINCT acorn_category FROM staging_household ORDER BY acorn_category;

INSERT INTO acorn_group (acorn_code, category_id)
SELECT DISTINCT s.acorn_code, c.category_id
FROM staging_household s
JOIN acorn_category c ON c.category_name = s.acorn_category
ORDER BY s.acorn_code;

INSERT INTO household (household_id, acorn_code, tariff_id)
SELECT s.household_id, s.acorn_code, t.tariff_id
FROM staging_household s
JOIN tariff t ON t.tariff_code = s.tariff;

-- 3. Weather (must load before readings: readings reference the weather hour) ---------
COPY weather_hourly (weather_hour, temperature_c, humidity, wind_speed,
                     pressure_hpa, visibility_km, conditions)
FROM '/data/clean/weather_hourly_clean.csv' WITH (FORMAT csv, HEADER true);

-- 4. Hourly readings (about 4.4 million rows; this step takes a minute or two) ---------
COPY reading_hourly (household_id, reading_hour, energy_kwh, is_imputed)
FROM '/data/clean/london_hourly_clean.csv' WITH (FORMAT csv, HEADER true);

COMMIT;

ANALYZE;

-- 5. Check: row counts per table ---------------------------------------------------------
SELECT 'acorn_category' AS table_name, COUNT(*) AS row_count FROM acorn_category
UNION ALL SELECT 'acorn_group',    COUNT(*) FROM acorn_group
UNION ALL SELECT 'tariff',         COUNT(*) FROM tariff
UNION ALL SELECT 'household',      COUNT(*) FROM household
UNION ALL SELECT 'calendar_month', COUNT(*) FROM calendar_month
UNION ALL SELECT 'weather_hourly', COUNT(*) FROM weather_hourly
UNION ALL SELECT 'reading_hourly', COUNT(*) FROM reading_hourly;
