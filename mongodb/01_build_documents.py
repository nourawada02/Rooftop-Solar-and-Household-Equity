"""Step 3.1 - Reshape the cleaned solar data into MongoDB documents.

Two collections are produced:

  solar_systems   one small document per system (2 modelled PVGIS systems and
                  44 real inverters): where it is, what it is, how it is set up.

  solar_daily     one document per system per day. The day's readings are EMBEDDED
                  as an array: 24 hourly readings for a PVGIS system, up to 96
                  fifteen-minute readings for an inverter.

The two kinds of system record different fields (a PVGIS reading has irradiance and
sun height; an inverter reading has DC/AC power and a fault flag). A document store
holds both shapes in one collection without empty columns.

Run from the project root (after cleaning/03_clean_data.py):
    python mongodb/01_build_documents.py

Outputs (MongoDB Extended JSON, one document per line):
    data/clean/solar_systems.jsonl
    data/clean/solar_daily.jsonl
"""

import json
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW = PROJECT_ROOT / "data" / "raw"
CLEAN = PROJECT_ROOT / "data" / "clean"

SEASON_OF_MONTH = {12: "Winter", 1: "Winter", 2: "Winter", 3: "Spring", 4: "Spring", 5: "Spring",
                   6: "Summer", 7: "Summer", 8: "Summer", 9: "Autumn", 10: "Autumn", 11: "Autumn"}
READINGS_PER_INVERTER_DAY = 96        # one every 15 minutes
FAULT_MIN_IRRADIATION = 0.1           # same threshold as the cleaning step


def mongo_date(timestamp):
    """A date in MongoDB Extended JSON, so mongoimport and pymongo store a real date."""
    return {"$date": timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")}


def number(value, digits=4):
    return round(float(value), digits)


# ------------------------------------------------------------------ PVGIS (modelled)
def pvgis_documents():
    systems, days = [], []
    hourly_all = pd.read_csv(CLEAN / "pvgis_hourly_clean.csv", parse_dates=["solar_hour_utc"])

    for city in ["london", "beirut"]:
        settings = json.loads((RAW / "pvgis" / f"pvgis_{city}_2013.json").read_text())["inputs"]
        mounting = settings["mounting_system"]["fixed"]
        system_id = f"pvgis_{city}"

        systems.append({
            "_id": system_id,
            "kind": "modelled",
            "source": "PVGIS 5.3 (EU Joint Research Centre)",
            "city": city,
            "location": {"type": "Point",                       # GeoJSON: [longitude, latitude]
                         "coordinates": [settings["location"]["longitude"],
                                         settings["location"]["latitude"]]},
            "elevation_m": settings["location"]["elevation"],
            "capacity_kwp": settings["pv_module"]["peak_power"],
            "setup": {
                "technology": settings["pv_module"]["technology"],
                "system_loss_pct": settings["pv_module"]["system_loss"],
                "slope_deg": mounting["slope"]["value"],
                "azimuth_deg": mounting["azimuth"]["value"],
                "mounting": mounting["type"],
                "radiation_database": settings["meteo_data"]["radiation_db"],
            },
        })

        city_hours = hourly_all[hourly_all["city"] == city]
        for date, day in city_hours.groupby(city_hours["solar_hour_utc"].dt.normalize()):
            readings = [{
                "ts": mongo_date(row.solar_hour_utc),
                "hour": int(row.solar_hour_utc.hour),
                "pv_kwh_per_kwp": number(row.pv_kwh_per_kwp, 5),
                "irradiance_w_m2": number(row.irradiance_w_m2, 2),
                "sun_height_deg": number(row.sun_height_deg, 2),
                "air_temp_c": number(row.air_temp_c, 2),
                "wind_speed_m_s": number(row.wind_speed_m_s, 2),
            } for row in day.itertuples()]

            days.append({
                "system_id": system_id,
                "kind": "modelled",
                "city": city,
                "date": mongo_date(date),
                "month": int(date.month),
                "season": SEASON_OF_MONTH[date.month],
                "summary": {
                    "energy_kwh_per_kwp": number(day["pv_kwh_per_kwp"].sum()),
                    "peak_kw_per_kwp": number(day["pv_kwh_per_kwp"].max()),
                    "sun_hours": int((day["pv_kwh_per_kwp"] > 0).sum()),
                },
                "readings": readings,
            })
    return systems, days


# ------------------------------------------------------------------ Inverters (measured)
def inverter_documents():
    systems, days = [], []
    log = pd.read_csv(CLEAN / "inverter_readings_clean.csv", parse_dates=["reading_ts"])

    for (plant, inverter_id), inverter_log in log.groupby(["plant", "inverter_id"]):
        system_id = f"{plant}:{inverter_id}"
        systems.append({
            "_id": system_id,
            "kind": "inverter",
            "source": "Kaggle: Solar Power Generation Data",
            "plant": plant,
            "plant_id": int(inverter_log["plant_id"].iloc[0]),
            "inverter_id": inverter_id,
            "country": "India",
            "interval_minutes": 15,
        })

        for date, day in inverter_log.groupby(inverter_log["reading_ts"].dt.normalize()):
            readings = []
            for row in day.itertuples():
                reading = {
                    "ts": mongo_date(row.reading_ts),
                    "hour": int(row.reading_ts.hour),
                    "dc_power_kw": number(row.dc_power_kw),
                    "ac_power_kw": number(row.ac_power_kw),
                    "daily_yield_kwh": number(row.daily_yield_kwh),
                    "is_fault": bool(row.is_fault),
                }
                has_sensor_reading = pd.notna(row.irradiation)
                if has_sensor_reading:                 # field is simply absent otherwise
                    reading["irradiation"] = number(row.irradiation)
                    reading["module_temp_c"] = number(row.module_temp_c, 2)
                    reading["ambient_temp_c"] = number(row.ambient_temp_c, 2)
                readings.append(reading)

            sunlit = day["irradiation"] > FAULT_MIN_IRRADIATION
            days.append({
                "system_id": system_id,
                "kind": "inverter",
                "plant": plant,
                "date": mongo_date(date),
                "month": int(date.month),
                "season": SEASON_OF_MONTH[date.month],
                "summary": {
                    "energy_kwh": number(day["ac_power_kw"].sum() * 0.25),   # kW x 15 min
                    "peak_ac_kw": number(day["ac_power_kw"].max()),
                    "readings": len(day),
                    "missing_readings": READINGS_PER_INVERTER_DAY - len(day),
                    "sunlit_readings": int(sunlit.sum()),
                    "fault_readings": int(day["is_fault"].sum()),
                },
                "readings": readings,
            })
    return systems, days


def write_json_lines(documents, path):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for document in documents:
            f.write(json.dumps(document) + "\n")
    print(f"{len(documents):,} documents -> {path}")


def main():
    pvgis_systems, pvgis_days = pvgis_documents()
    inverter_systems, inverter_days = inverter_documents()
    write_json_lines(pvgis_systems + inverter_systems, CLEAN / "solar_systems.jsonl")
    write_json_lines(pvgis_days + inverter_days, CLEAN / "solar_daily.jsonl")
    embedded = sum(len(d["readings"]) for d in pvgis_days + inverter_days)
    print(f"Embedded readings in total: {embedded:,}")


if __name__ == "__main__":
    main()
