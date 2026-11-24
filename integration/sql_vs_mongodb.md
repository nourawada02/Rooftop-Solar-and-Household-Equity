# PostgreSQL versus MongoDB: what we learned

DRAFT for the team to edit. Every point below comes from something that happened in
this project; replace or extend them with your own experience before submitting.

## Where each part of the data went, and why

| Data | Store | Reason |
| --- | --- | --- |
| Households, ACORN groups, tariffs | PostgreSQL | Fixed columns and strict relationships: every household has exactly one group and one tariff |
| Hourly meter readings (4.4 million rows) | PostgreSQL | One identical shape per row; the questions are joins and aggregations across tables |
| Weather | PostgreSQL | Tabular, one row per hour, joined to readings on the hour |
| PVGIS modelled solar output | MongoDB | Arrives as nested JSON (settings, hourly output, metadata) |
| Inverter logs | MongoDB | Different fields from the PVGIS readings; some readings have no sensor values |
| Simulation results | PostgreSQL | Tabular and read by Power BI through relationships |

## What was easier in PostgreSQL

- **Combining tables.** Comparing categories needed household, ACORN group, category,
  tariff, weather and month in one query. A five-table JOIN (Q2) is one statement.
- **Protecting data quality.** Constraints did real work: the foreign key from readings
  to weather guarantees every reading has a weather hour, and CHECK constraints reject
  a negative reading or an unknown category before it reaches an analysis.
- **Rankings and trends.** RANK and a 7-day moving average (Q5, Q6) are single window
  functions.
- **Loading volume.** COPY loaded 4,371,602 rows in one command.

## What was easier in MongoDB

- **Two device types in one collection.** A modelled reading has irradiance and sun
  height; an inverter reading has DC/AC power and a fault flag. Both live in
  `solar_daily` with no empty columns and no schema change.
- **A day as one unit.** Each document holds the whole day's readings, so a daily curve
  is one read, and the `summary` sub-document answers most questions without opening
  the array.
- **Importing JSON as it is.** The PVGIS settings went in as a nested sub-document with
  no flattening.
- **Questions about arrays.** "Days with at least one outage in strong sun" is a single
  `$elemMatch` (Q7).

## What was harder than expected

- **PostgreSQL:** a correlated subquery over a view (the first version of Q7) re-read
  all 4.4 million readings once per household and did not finish. Materialising the
  inner result in a CTE fixed it. The lesson: SQL hides the cost of a query until the
  data is large.
- **PostgreSQL:** readings are stored in UTC but behaviour follows local time, so every
  "evening" query needed a time-zone conversion (the `v_reading_local` view).
- **MongoDB:** nothing stops a wrongly shaped document from being inserted. The
  correctness that PostgreSQL enforces with constraints had to come from our own
  build script.
- **MongoDB:** joins are possible (`$lookup`, Q6) but more verbose than a SQL JOIN, so
  we kept them to the one place they were needed.
- **Both:** connecting from Windows reached a different MongoDB already installed on
  the computer; running the tools inside the Docker container avoided it.

## How the two were linked

The shared key is **location and hour**: `reading_hourly.reading_hour` in PostgreSQL and
`solar_daily.readings.ts` in MongoDB are both UTC hours for London. The integration
script reads demand from PostgreSQL and solar output from MongoDB, lines them up hour by
hour, and simulates each household with solar and a battery.

## Our conclusion

Neither store could have done the whole project well. PostgreSQL suited the data whose
value comes from relationships and guarantees; MongoDB suited the data whose shape
varies by source. The cost of using both was the integration step, which had to live in
Python because neither database can query the other.
