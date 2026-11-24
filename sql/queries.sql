-- queries.sql
-- Guiding question: would rooftop solar cut grid demand most for the households that
-- need it most (the lowest-income "Adversity" category)?
--
-- Solar only helps a household that uses electricity while the sun is up, so most of
-- these queries ask WHO uses HOW MUCH, and WHEN.
--
-- Run from the project root (PowerShell):
--   Get-Content sql/queries.sql | docker compose exec -T postgres psql -U solar -d solar_equity
--
-- Time note: readings are stored in UTC. People live on local time, so "evening" and
-- "daytime" are defined on London local time (UTC in winter, UTC+1 in summer).
--   daytime (solar hours) = 10:00-15:59 local      evening peak = 17:00-20:59 local


-- ---------------------------------------------------------------------------------------
-- VIEW A (helper). Adds London local time to every reading.
-- Shows: each reading with its local hour, local date and month, ready for grouping.
-- ---------------------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_reading_local AS
SELECT
    r.household_id,
    r.reading_hour,
    (r.reading_hour AT TIME ZONE 'UTC') AT TIME ZONE 'Europe/London'  AS local_ts,
    EXTRACT(HOUR  FROM (r.reading_hour AT TIME ZONE 'UTC') AT TIME ZONE 'Europe/London')::INT AS local_hour,
    EXTRACT(MONTH FROM (r.reading_hour AT TIME ZONE 'UTC') AT TIME ZONE 'Europe/London')::INT AS month_no,
    ((r.reading_hour AT TIME ZONE 'UTC') AT TIME ZONE 'Europe/London')::DATE AS local_date,
    r.energy_kwh,
    r.is_imputed
FROM reading_hourly r;


-- ---------------------------------------------------------------------------------------
-- Q1. Multi-table JOIN (4 tables).
-- Shows: average daily electricity use per household in each socio-economic category.
-- ---------------------------------------------------------------------------------------
SELECT
    c.category_name,
    COUNT(DISTINCT h.household_id)        AS households,
    ROUND(AVG(r.energy_kwh) * 24, 2)      AS avg_kwh_per_household_day
FROM reading_hourly r
JOIN household      h ON h.household_id = r.household_id
JOIN acorn_group    g ON g.acorn_code   = h.acorn_code
JOIN acorn_category c ON c.category_id  = g.category_id
GROUP BY c.category_name
ORDER BY avg_kwh_per_household_day DESC;


-- ---------------------------------------------------------------------------------------
-- Q2. Multi-table JOIN with weather (5 tables).
-- Shows: how hourly demand in each category changes with outdoor temperature.
-- ---------------------------------------------------------------------------------------
SELECT
    c.category_name,
    CASE
        WHEN w.temperature_c <  5 THEN '1. below 5 C'
        WHEN w.temperature_c < 10 THEN '2. 5 to 10 C'
        WHEN w.temperature_c < 15 THEN '3. 10 to 15 C'
        WHEN w.temperature_c < 20 THEN '4. 15 to 20 C'
        ELSE                           '5. 20 C and above'
    END                              AS temperature_band,
    ROUND(AVG(r.energy_kwh), 3)      AS avg_kwh_per_hour,
    COUNT(*)                         AS readings
FROM reading_hourly r
JOIN weather_hourly w ON w.weather_hour = r.reading_hour
JOIN household      h ON h.household_id = r.household_id
JOIN acorn_group    g ON g.acorn_code   = h.acorn_code
JOIN acorn_category c ON c.category_id  = g.category_id
GROUP BY c.category_name, temperature_band
ORDER BY c.category_name, temperature_band;


-- ---------------------------------------------------------------------------------------
-- Q3. GROUP BY with HAVING.
-- Shows: the ACORN groups whose demand is concentrated in the evening peak (more than
-- 25% of their electricity between 17:00 and 20:59), i.e. the groups solar helps least
-- without a battery.
-- ---------------------------------------------------------------------------------------
SELECT
    h.acorn_code,
    COUNT(DISTINCT v.household_id)                                             AS households,
    ROUND(100.0 * SUM(v.energy_kwh) FILTER (WHERE v.local_hour BETWEEN 17 AND 20)
                / SUM(v.energy_kwh), 1)                                        AS evening_share_pct
FROM v_reading_local v
JOIN household h ON h.household_id = v.household_id
GROUP BY h.acorn_code
HAVING SUM(v.energy_kwh) FILTER (WHERE v.local_hour BETWEEN 17 AND 20)
       > 0.25 * SUM(v.energy_kwh)
ORDER BY evening_share_pct DESC;


-- ---------------------------------------------------------------------------------------
-- Q4. CTE (common table expression).
-- Shows: for each category, the share of electricity used in solar hours, in the
-- evening peak, and at other times. A higher daytime share means more of a rooftop
-- system's output can be used directly.
-- ---------------------------------------------------------------------------------------
WITH labelled AS (
    SELECT
        c.category_name,
        CASE
            WHEN v.local_hour BETWEEN 10 AND 15 THEN 'daytime'
            WHEN v.local_hour BETWEEN 17 AND 20 THEN 'evening'
            ELSE                                     'other'
        END           AS period,
        v.energy_kwh
    FROM v_reading_local v
    JOIN household      h ON h.household_id = v.household_id
    JOIN acorn_group    g ON g.acorn_code   = h.acorn_code
    JOIN acorn_category c ON c.category_id  = g.category_id
),
totals AS (
    SELECT category_name, period, SUM(energy_kwh) AS kwh
    FROM labelled
    GROUP BY category_name, period
)
SELECT
    category_name,
    ROUND(100.0 * SUM(kwh) FILTER (WHERE period = 'daytime') / SUM(kwh), 1) AS daytime_share_pct,
    ROUND(100.0 * SUM(kwh) FILTER (WHERE period = 'evening') / SUM(kwh), 1) AS evening_share_pct,
    ROUND(100.0 * SUM(kwh) FILTER (WHERE period = 'other')   / SUM(kwh), 1) AS other_share_pct
FROM totals
GROUP BY category_name
ORDER BY daytime_share_pct DESC;


-- ---------------------------------------------------------------------------------------
-- Q5. Window function: RANK.
-- Shows: every ACORN group ranked by average evening-peak demand, both overall and
-- inside its own category.
-- ---------------------------------------------------------------------------------------
WITH group_evening AS (
    SELECT
        c.category_name,
        h.acorn_code,
        AVG(v.energy_kwh) AS avg_evening_kwh
    FROM v_reading_local v
    JOIN household      h ON h.household_id = v.household_id
    JOIN acorn_group    g ON g.acorn_code   = h.acorn_code
    JOIN acorn_category c ON c.category_id  = g.category_id
    WHERE v.local_hour BETWEEN 17 AND 20
    GROUP BY c.category_name, h.acorn_code
)
SELECT
    category_name,
    acorn_code,
    ROUND(avg_evening_kwh, 3)                                               AS avg_evening_kwh_per_hour,
    RANK() OVER (ORDER BY avg_evening_kwh DESC)                             AS rank_overall,
    RANK() OVER (PARTITION BY category_name ORDER BY avg_evening_kwh DESC)  AS rank_in_category
FROM group_evening
ORDER BY rank_overall;


-- ---------------------------------------------------------------------------------------
-- Q6. Window function: moving average.
-- Shows: daily electricity use per household in each category, smoothed with a 7-day
-- moving average so the seasonal trend is visible (first 10 days of July shown).
-- ---------------------------------------------------------------------------------------
WITH daily AS (
    SELECT
        c.category_name,
        v.local_date,
        SUM(v.energy_kwh) / COUNT(DISTINCT v.household_id) AS kwh_per_household
    FROM v_reading_local v
    JOIN household      h ON h.household_id = v.household_id
    JOIN acorn_group    g ON g.acorn_code   = h.acorn_code
    JOIN acorn_category c ON c.category_id  = g.category_id
    GROUP BY c.category_name, v.local_date
),
smoothed AS (
    SELECT
        category_name,
        local_date,
        ROUND(kwh_per_household, 2) AS kwh_per_household,
        ROUND(AVG(kwh_per_household) OVER (
                  PARTITION BY category_name
                  ORDER BY local_date
                  ROWS BETWEEN 6 PRECEDING AND CURRENT ROW), 2) AS moving_avg_7_days
    FROM daily
)
SELECT *
FROM smoothed
WHERE local_date BETWEEN DATE '2013-07-01' AND DATE '2013-07-10'
ORDER BY category_name, local_date;


-- ---------------------------------------------------------------------------------------
-- VIEW B. Annual total per household.
-- Shows: each household's electricity use for 2013 with its category and tariff.
-- ---------------------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_household_annual AS
SELECT
    h.household_id,
    c.category_name,
    h.acorn_code,
    t.tariff_code,
    SUM(r.energy_kwh)                              AS annual_kwh,
    COUNT(*)                                       AS hours_recorded,
    SUM(CASE WHEN r.is_imputed THEN 1 ELSE 0 END)  AS hours_imputed
FROM reading_hourly r
JOIN household      h ON h.household_id = r.household_id
JOIN acorn_group    g ON g.acorn_code   = h.acorn_code
JOIN acorn_category c ON c.category_id  = g.category_id
JOIN tariff         t ON t.tariff_id    = h.tariff_id
GROUP BY h.household_id, c.category_name, h.acorn_code, t.tariff_code;


-- ---------------------------------------------------------------------------------------
-- Q7. CTE + correlated subquery.
-- Shows: how many households in each category use more than twice their own
-- category's average, i.e. the heavy users that pull the category average up.
-- The CTE is MATERIALIZED so the annual totals are computed once; without it the
-- subquery would re-read all 4.4 million readings for every household.
-- ---------------------------------------------------------------------------------------
WITH annual AS MATERIALIZED (
    SELECT household_id, category_name, annual_kwh
    FROM v_household_annual
)
SELECT
    a.category_name,
    COUNT(*)                     AS heavy_users,
    ROUND(MIN(a.annual_kwh), 0)  AS lowest_heavy_user_kwh,
    ROUND(MAX(a.annual_kwh), 0)  AS highest_heavy_user_kwh
FROM annual a
WHERE a.annual_kwh > 2 * (
        SELECT AVG(b.annual_kwh)
        FROM annual b
        WHERE b.category_name = a.category_name)
GROUP BY a.category_name
ORDER BY heavy_users DESC;


-- ---------------------------------------------------------------------------------------
-- Q8. JOIN with the month lookup table.
-- Shows: average daily use per household by season and category, to compare with the
-- season when solar output is highest.
-- ---------------------------------------------------------------------------------------
SELECT
    m.season,
    c.category_name,
    ROUND(AVG(v.energy_kwh) * 24, 2) AS avg_kwh_per_household_day
FROM v_reading_local v
JOIN calendar_month m ON m.month_no     = v.month_no
JOIN household      h ON h.household_id = v.household_id
JOIN acorn_group    g ON g.acorn_code   = h.acorn_code
JOIN acorn_category c ON c.category_id  = g.category_id
GROUP BY m.season, c.category_name
ORDER BY CASE m.season WHEN 'Winter' THEN 1 WHEN 'Spring' THEN 2
                       WHEN 'Summer' THEN 3 ELSE 4 END,
         c.category_name;


-- ---------------------------------------------------------------------------------------
-- Q9. JOIN with the tariff table.
-- Shows: whether households on the time-of-use tariff shift demand out of the evening
-- peak, compared with standard-tariff households in the same category.
-- ---------------------------------------------------------------------------------------
SELECT
    c.category_name,
    t.tariff_code,
    COUNT(DISTINCT v.household_id)                                          AS households,
    ROUND(100.0 * SUM(v.energy_kwh) FILTER (WHERE v.local_hour BETWEEN 17 AND 20)
                / SUM(v.energy_kwh), 1)                                     AS evening_share_pct,
    ROUND(100.0 * SUM(v.energy_kwh) FILTER (WHERE v.local_hour BETWEEN 10 AND 15)
                / SUM(v.energy_kwh), 1)                                     AS daytime_share_pct
FROM v_reading_local v
JOIN household      h ON h.household_id = v.household_id
JOIN acorn_group    g ON g.acorn_code   = h.acorn_code
JOIN acorn_category c ON c.category_id  = g.category_id
JOIN tariff         t ON t.tariff_id    = h.tariff_id
GROUP BY c.category_name, t.tariff_code
ORDER BY c.category_name, t.tariff_code;


-- ---------------------------------------------------------------------------------------
-- VIEW C (used by Power BI). The average day of each category in each season.
-- Shows: for every category, season and local hour of the day, the average kWh one
-- household uses in that hour. Power BI draws the daily demand curves from this view.
-- ---------------------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_powerbi_category_profile AS
SELECT
    c.category_name,
    m.season,
    v.local_hour,
    ROUND(AVG(v.energy_kwh), 4)      AS avg_kwh_per_household,
    COUNT(DISTINCT v.household_id)   AS households,
    COUNT(*)                         AS readings
FROM v_reading_local v
JOIN calendar_month m ON m.month_no     = v.month_no
JOIN household      h ON h.household_id = v.household_id
JOIN acorn_group    g ON g.acorn_code   = h.acorn_code
JOIN acorn_category c ON c.category_id  = g.category_id
GROUP BY c.category_name, m.season, v.local_hour;


-- ---------------------------------------------------------------------------------------
-- Q10. Query on the Power BI view.
-- Shows: the hour of the day when each category's demand peaks, in each season.
-- ---------------------------------------------------------------------------------------
SELECT DISTINCT ON (category_name, season)
    category_name,
    season,
    local_hour            AS peak_hour_local,
    avg_kwh_per_household AS peak_kwh_per_household
FROM v_powerbi_category_profile
ORDER BY category_name, season, avg_kwh_per_household DESC;
