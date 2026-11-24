-- simulation_schema.sql
-- Tables written by integration/01_integrate_and_simulate.py and read by Power BI.
--
--   solar_profile   the average solar day per city and season (comes from MongoDB)
--   sim_scenario    one row per what-if scenario (city, panel size, battery size, ...)
--   sim_result      the outcome of each scenario for each household and season
--
-- The script runs this file itself; it can be re-run.

DROP TABLE IF EXISTS sim_result, sim_scenario, solar_profile CASCADE;

CREATE TABLE solar_profile (
    city         VARCHAR(10) NOT NULL CHECK (city IN ('london', 'beirut')),
    season       VARCHAR(6)  NOT NULL CHECK (season IN ('Winter', 'Spring', 'Summer', 'Autumn')),
    clock_hour   SMALLINT    NOT NULL CHECK (clock_hour BETWEEN 0 AND 23),   -- local clock time
    kwh_per_kwp  NUMERIC(7, 5) NOT NULL CHECK (kwh_per_kwp >= 0),
    PRIMARY KEY (city, season, clock_hour)
);

CREATE TABLE sim_scenario (
    scenario_id         SMALLINT PRIMARY KEY,
    city                VARCHAR(10)  NOT NULL CHECK (city IN ('london', 'beirut')),
    performance         VARCHAR(20)  NOT NULL,
    performance_factor  NUMERIC(4, 3) NOT NULL CHECK (performance_factor > 0 AND performance_factor <= 1),
    panel_kwp           NUMERIC(4, 1) NOT NULL CHECK (panel_kwp > 0),
    battery_kwh         NUMERIC(4, 1) NOT NULL CHECK (battery_kwh >= 0),
    UNIQUE (city, performance, panel_kwp, battery_kwh)
);

CREATE TABLE sim_result (
    scenario_id        SMALLINT NOT NULL REFERENCES sim_scenario (scenario_id),
    household_id       CHAR(9)  NOT NULL REFERENCES household (household_id),
    season             VARCHAR(6) NOT NULL CHECK (season IN ('Winter', 'Spring', 'Summer', 'Autumn')),
    demand_kwh         NUMERIC(10, 3) NOT NULL CHECK (demand_kwh >= 0),
    solar_kwh          NUMERIC(10, 3) NOT NULL CHECK (solar_kwh >= 0),
    self_consumed_kwh  NUMERIC(10, 3) NOT NULL CHECK (self_consumed_kwh >= 0),  -- solar used at home
    grid_import_kwh    NUMERIC(10, 3) NOT NULL CHECK (grid_import_kwh >= 0),    -- still bought
    export_kwh         NUMERIC(10, 3) NOT NULL CHECK (export_kwh >= 0),         -- surplus sent out
    PRIMARY KEY (scenario_id, household_id, season)
);
