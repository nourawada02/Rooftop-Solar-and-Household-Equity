"""Download modelled hourly solar output from the PVGIS API for London and Beirut.

Writes one raw JSON file per city into data/raw/pvgis/. The output is for a
1 kWp system, so it can be scaled to any panel size later.

API documentation:
https://joint-research-centre.ec.europa.eu/photovoltaic-geographical-information-system-pvgis/getting-started-pvgis/api-non-interactive-service_en
"""

import json
from pathlib import Path

import requests

PVGIS_URL = "https://re.jrc.ec.europa.eu/api/v5_3/seriescalc"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "pvgis"

# The smart-meter data covers 2011-2014; 2013 is the only full calendar year.
YEAR = 2013

LOCATIONS = {
    "london": {"lat": 51.507, "lon": -0.128},
    "beirut": {"lat": 33.894, "lon": 35.502},
}


def fetch_city(name, coordinates):
    params = {
        "lat": coordinates["lat"],
        "lon": coordinates["lon"],
        "startyear": YEAR,
        "endyear": YEAR,
        "pvcalculation": 1,   # return PV power, not only irradiance
        "peakpower": 1,       # 1 kWp system
        "loss": 14,           # PVGIS default system loss, in percent
        "optimalangles": 1,   # let PVGIS choose tilt and orientation
        "outputformat": "json",
    }
    response = requests.get(PVGIS_URL, params=params, timeout=120)
    response.raise_for_status()
    payload = response.json()

    hourly_records = payload["outputs"]["hourly"]
    output_path = OUTPUT_DIR / f"pvgis_{name}_{YEAR}.json"
    output_path.write_text(json.dumps(payload))
    print(f"{name}: saved {len(hourly_records)} hourly records to {output_path}")


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, coordinates in LOCATIONS.items():
        fetch_city(name, coordinates)


if __name__ == "__main__":
    main()
