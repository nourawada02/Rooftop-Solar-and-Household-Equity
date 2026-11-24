# MongoDB document schema

Database: `solar_equity`. Two collections hold the solar side of the project.

| Collection | One document per | Documents | Role |
| --- | --- | --- | --- |
| `solar_systems` | solar system | 46 (2 modelled, 44 inverters) | Reference data: where the system is and how it is set up |
| `solar_daily` | system per day | 2,194 | The readings, embedded as an array (153,996 in total) |

## Why this data is in MongoDB and not PostgreSQL

The two kinds of system record different things:

- A **modelled** PVGIS reading has irradiance, sun height, air temperature and wind speed.
- An **inverter** reading has DC power, AC power, daily yield, a fault flag, and
  sometimes plant sensor values (4 readings have none, so those fields are absent).

In a relational table this would need many columns that are empty for half the rows,
or one table per kind of device. A document collection stores both shapes side by side.
The PVGIS settings are also nested by nature (location, mounting, module), which maps
directly to a sub-document.

## `solar_systems`

```json
{
  "_id": "pvgis_beirut",
  "kind": "modelled",
  "source": "PVGIS 5.3 (EU Joint Research Centre)",
  "city": "beirut",
  "location": { "type": "Point", "coordinates": [35.502, 33.894] },
  "elevation_m": 40.0,
  "capacity_kwp": 1.0,
  "setup": {
    "technology": "c-Si", "system_loss_pct": 14.0,
    "slope_deg": 30, "azimuth_deg": -8,
    "mounting": "free-standing", "radiation_database": "PVGIS-SARAH3"
  }
}
```

An inverter document has `kind: "inverter"`, `plant`, `plant_id`, `inverter_id`,
`country` and `interval_minutes` instead of a location and setup.

## `solar_daily`

Modelled system (24 hourly readings):

```json
{
  "system_id": "pvgis_beirut",
  "kind": "modelled",
  "city": "beirut",
  "date": { "$date": "2013-07-15T00:00:00Z" },
  "month": 7,
  "season": "Summer",
  "summary": { "energy_kwh_per_kwp": 5.6, "peak_kw_per_kwp": 0.72, "sun_hours": 14 },
  "readings": [
    { "ts": { "$date": "2013-07-15T09:00:00Z" }, "hour": 9, "pv_kwh_per_kwp": 0.7,
      "irradiance_w_m2": 930.0, "sun_height_deg": 71.2, "air_temp_c": 28.9, "wind_speed_m_s": 2.1 }
  ]
}
```

Inverter (up to 96 fifteen-minute readings):

```json
{
  "system_id": "plant_2:81aHJ1q11NBPMrL",
  "kind": "inverter",
  "plant": "plant_2",
  "date": { "$date": "2020-05-27T00:00:00Z" },
  "month": 5,
  "season": "Spring",
  "summary": { "energy_kwh": 1317.5, "peak_ac_kw": 1180.2, "readings": 96,
               "missing_readings": 0, "sunlit_readings": 44, "fault_readings": 33 },
  "readings": [
    { "ts": { "$date": "2020-05-27T11:15:00Z" }, "hour": 11, "dc_power_kw": 0.0,
      "ac_power_kw": 0.0, "daily_yield_kwh": 1520.0, "is_fault": true,
      "irradiation": 0.83, "module_temp_c": 55.1, "ambient_temp_c": 33.4 }
  ]
}
```

(Values in the examples are illustrative; field names and structure are exact.)

## Embed or reference?

**Readings are embedded in the day.**

- They are always read together: every question asks about a day, a season or an
  hour-of-day pattern, never about one isolated reading.
- The array is bounded: at most 24 or 96 entries, so a document can never grow
  without limit (the main risk of embedding).
- One read returns a whole day; no join is needed to rebuild a daily curve.
- The `summary` sub-document stores the day's totals, so most queries do not need
  to open the array at all.

**System details are referenced** (`solar_daily.system_id` points to `solar_systems._id`).

- They are the same for every day of a system. Embedding them would repeat the
  location and setup 365 times and make a correction require 365 updates.
- When they are needed they are joined with `$lookup` (query Q6).

## Link to PostgreSQL

The shared key is **location and hour**. `solar_daily.city = "london"` with
`readings.ts` (UTC, on the hour) matches `reading_hourly.reading_hour` (UTC) in
PostgreSQL, so household demand and solar output can be compared hour by hour.

## Index

`{ system_id: 1, date: 1 }`, unique: one document per system per day. It supports the
most common lookup (one system, one day or a date range) and prevents the same day
from being imported twice. `queries.js` (Q8) shows its effect with `explain()`.
