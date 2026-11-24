-- schema.sql
-- DRAFT: column names and constraints will be finalised after the data profile.
-- Target: 3NF, at least 3 related tables, primary and foreign keys, NOT NULL / CHECK / UNIQUE.

CREATE TABLE acorn_group (
    acorn_group_id  SERIAL PRIMARY KEY,
    acorn_code      VARCHAR(10) NOT NULL UNIQUE,   -- e.g. ACORN-A
    category        VARCHAR(20) NOT NULL           -- Affluent / Comfortable / Adversity
);

CREATE TABLE tariff (
    tariff_id    SERIAL PRIMARY KEY,
    tariff_name  VARCHAR(20) NOT NULL UNIQUE       -- Std / ToU
);

CREATE TABLE household (
    household_id    VARCHAR(20) PRIMARY KEY,       -- meter id from the dataset
    acorn_group_id  INT NOT NULL REFERENCES acorn_group (acorn_group_id),
    tariff_id       INT NOT NULL REFERENCES tariff (tariff_id)
);

CREATE TABLE reading (
    household_id  VARCHAR(20) NOT NULL REFERENCES household (household_id),
    reading_ts    TIMESTAMPTZ NOT NULL,
    energy_kwh    NUMERIC(8, 3) NOT NULL CHECK (energy_kwh >= 0),
    is_flagged    BOOLEAN NOT NULL DEFAULT FALSE,  -- set by faulty-meter detection
    PRIMARY KEY (household_id, reading_ts)
);

CREATE TABLE weather_hourly (
    weather_ts     TIMESTAMPTZ PRIMARY KEY,
    temperature_c  NUMERIC(5, 2),
    cloud_cover    NUMERIC(4, 2) CHECK (cloud_cover BETWEEN 0 AND 1)
);
