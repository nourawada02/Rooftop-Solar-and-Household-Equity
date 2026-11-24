-- schema.sql
-- Rooftop Solar and Household Equity: relational schema (PostgreSQL 15+).
--
-- Seven tables in third normal form (3NF):
--   acorn_category  the three socio-economic categories compared in the project
--   acorn_group     the detailed ACORN codes; each belongs to exactly one category
--   tariff          the two electricity tariffs in the trial
--   household       one row per sampled household
--   calendar_month  month -> season lookup
--   weather_hourly  London weather, one row per hour of 2013
--   reading_hourly  electricity use, one row per household per hour
--
-- Why 3NF: a household's category is NOT stored on the household. It depends on the
-- ACORN code (household -> acorn_code -> category), so it lives in acorn_group. That
-- way a code can never be given two different categories by mistake.
--
-- All timestamps are UTC, stored without a time zone (see the cleaning log, entry 10).
-- The script can be re-run: it drops and recreates everything.

DROP VIEW  IF EXISTS v_powerbi_category_profile, v_household_annual, v_reading_local CASCADE;
DROP TABLE IF EXISTS reading_hourly, reading, weather_hourly, calendar_month,
                     household, tariff, acorn_group, acorn_category CASCADE;

CREATE TABLE acorn_category (
    category_id    SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    category_name  VARCHAR(20) NOT NULL UNIQUE
                   CHECK (category_name IN ('Adversity', 'Comfortable', 'Affluent'))
);

CREATE TABLE acorn_group (
    acorn_code   VARCHAR(10) PRIMARY KEY CHECK (acorn_code LIKE 'ACORN-_'),
    category_id  SMALLINT NOT NULL REFERENCES acorn_category (category_id)
);

CREATE TABLE tariff (
    tariff_id    SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tariff_code  VARCHAR(5)  NOT NULL UNIQUE CHECK (tariff_code IN ('Std', 'ToU')),
    description  VARCHAR(60) NOT NULL
);

CREATE TABLE household (
    household_id  CHAR(9) PRIMARY KEY CHECK (household_id LIKE 'MAC______'),
    acorn_code    VARCHAR(10) NOT NULL REFERENCES acorn_group (acorn_code),
    tariff_id     SMALLINT    NOT NULL REFERENCES tariff (tariff_id)
);

CREATE TABLE calendar_month (
    month_no    SMALLINT PRIMARY KEY CHECK (month_no BETWEEN 1 AND 12),
    month_name  VARCHAR(9)  NOT NULL UNIQUE,
    season      VARCHAR(6)  NOT NULL CHECK (season IN ('Winter', 'Spring', 'Summer', 'Autumn'))
);

CREATE TABLE weather_hourly (
    weather_hour   TIMESTAMP PRIMARY KEY,
    temperature_c  NUMERIC(5, 2) NOT NULL CHECK (temperature_c BETWEEN -30 AND 50),
    humidity       NUMERIC(4, 3) NOT NULL CHECK (humidity BETWEEN 0 AND 1),
    wind_speed     NUMERIC(5, 2) NOT NULL CHECK (wind_speed >= 0),
    pressure_hpa   NUMERIC(7, 2) NOT NULL CHECK (pressure_hpa BETWEEN 900 AND 1100),
    visibility_km  NUMERIC(5, 2) NOT NULL CHECK (visibility_km >= 0),
    conditions     VARCHAR(40)   NOT NULL
);

CREATE TABLE reading_hourly (
    household_id  CHAR(9)   NOT NULL REFERENCES household (household_id),
    reading_hour  TIMESTAMP NOT NULL REFERENCES weather_hourly (weather_hour),
    energy_kwh    NUMERIC(8, 4) NOT NULL CHECK (energy_kwh >= 0),
    is_imputed    BOOLEAN   NOT NULL DEFAULT FALSE,   -- TRUE if a half-hour was interpolated
    PRIMARY KEY (household_id, reading_hour)
);

-- The primary key already indexes (household_id, reading_hour).
-- This second index speeds up queries that group by time across all households.
CREATE INDEX idx_reading_hour ON reading_hourly (reading_hour);
