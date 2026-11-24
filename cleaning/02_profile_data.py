"""Step 1.2 - Profile every data source BEFORE cleaning.

For each file this reports: row count, column types, missing values, duplicates and
value ranges, plus the source-specific problems the cleaning step must handle.
Nothing is changed here; this script only reads.

Run from the project root (after 01_sample_households.py):
    python cleaning/02_profile_data.py

Output:
    cleaning/data_profile.md
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW = PROJECT_ROOT / "data" / "raw"
INTERIM = PROJECT_ROOT / "data" / "interim"
REPORT_PATH = PROJECT_ROOT / "cleaning" / "data_profile.md"

READINGS_IN_FULL_YEAR = 365 * 48
report_lines = []


def say(text=""):
    report_lines.append(text)


def table(headers, rows):
    say("| " + " | ".join(headers) + " |")
    say("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        say("| " + " | ".join(str(cell) for cell in row) + " |")
    say()


def percent(part, whole):
    return f"{100 * part / whole:.2f}%" if whole else "n/a"


def profile_columns(df):
    """Generic column profile: type, missing, distinct values, range."""
    rows = []
    for column in df.columns:
        series = df[column]
        missing = int(series.isna().sum())
        is_numeric = pd.api.types.is_numeric_dtype(series)
        is_datetime = pd.api.types.is_datetime64_any_dtype(series)
        if is_numeric:
            value_range = f"{series.min():.4g} to {series.max():.4g}"
        elif is_datetime:
            value_range = f"{series.min()} to {series.max()}"
        else:
            examples = series.dropna().astype(str).unique()[:3]
            value_range = "e.g. " + ", ".join(examples)
        rows.append([column, series.dtype, missing, percent(missing, len(df)),
                     series.nunique(), value_range])
    table(["Column", "Type", "Missing", "Missing %", "Distinct", "Range / examples"], rows)


# ---------------------------------------------------------------- London readings
def profile_london_readings():
    say("## 1. London half-hourly readings (sample of 500 households, 2013)")
    say()
    say("File: `data/interim/london_halfhourly_sample.csv` (raw rows, untouched)")
    say()

    path = INTERIM / "london_halfhourly_sample.csv"
    id_parts, time_parts, energy_parts = [], [], []
    for chunk in pd.read_csv(path, dtype=str, chunksize=1_000_000):
        chunk.columns = ["LCLid", "tstp", "energy_raw"]
        id_parts.append(chunk["LCLid"].astype("category"))
        time_parts.append(pd.to_datetime(chunk["tstp"].str.slice(0, 19)))
        energy_text = chunk["energy_raw"].str.strip()
        energy_parts.append(pd.to_numeric(energy_text, errors="coerce").astype("float32"))

    household = pd.concat(id_parts).astype("category")
    timestamp = pd.concat(time_parts)
    energy = pd.concat(energy_parts)
    total_rows = len(energy)

    not_a_number = int(energy.isna().sum())
    negative = int((energy < 0).sum())
    zero = int((energy == 0).sum())
    is_on_grid = (timestamp.dt.minute.isin([0, 30])) & (timestamp.dt.second == 0)
    off_grid = int((~is_on_grid).sum())

    keys = pd.DataFrame({"LCLid": household.cat.codes.to_numpy(),
                         "tstp": timestamp.to_numpy()})
    duplicate_keys = int(keys.duplicated().sum())
    unique_per_household = keys.drop_duplicates().groupby("LCLid").size()
    missing_per_household = READINGS_IN_FULL_YEAR - unique_per_household
    missing_intervals = int(missing_per_household.clip(lower=0).sum())
    expected_rows = READINGS_IN_FULL_YEAR * household.nunique()

    table(["Measure", "Value"], [
        ["Rows", f"{total_rows:,}"],
        ["Households", household.nunique()],
        ["Columns", "LCLid (text), tstp (text timestamp), energy(kWh/hh) (text)"],
        ["Time range", f"{timestamp.min()} to {timestamp.max()}"],
        ["Energy stored as text with spaces", "yes, every row (e.g. `' 0.263 '`)"],
        ["Energy not a number (`Null`)", f"{not_a_number:,} ({percent(not_a_number, total_rows)})"],
        ["Duplicate (household, timestamp) rows", f"{duplicate_keys:,} ({percent(duplicate_keys, total_rows)})"],
        ["Timestamps off the half-hour grid", f"{off_grid:,}"],
        ["Missing half-hours (vs full year)", f"{missing_intervals:,} ({percent(missing_intervals, expected_rows)})"],
        ["Households with at least one missing half-hour", int((missing_per_household > 0).sum())],
        ["Negative readings", f"{negative:,}"],
        ["Zero readings", f"{zero:,} ({percent(zero, total_rows)})"],
    ])

    say("**Energy per half-hour (kWh), valid readings only**")
    say()
    quantiles = energy.quantile([0.01, 0.25, 0.5, 0.75, 0.99, 0.999])
    table(["Min", "1%", "25%", "Median", "Mean", "75%", "99%", "99.9%", "Max"], [[
        f"{energy.min():.3f}", f"{quantiles[0.01]:.3f}", f"{quantiles[0.25]:.3f}",
        f"{quantiles[0.5]:.3f}", f"{energy.mean():.3f}", f"{quantiles[0.75]:.3f}",
        f"{quantiles[0.99]:.3f}", f"{quantiles[0.999]:.3f}", f"{energy.max():.3f}"]])

    per_household = pd.DataFrame({"LCLid": household, "energy": energy}).groupby(
        "LCLid", observed=True)["energy"]
    zero_share = per_household.apply(lambda values: (values == 0).mean())
    household_max = per_household.max()
    mostly_zero = int((zero_share > 0.5).sum())
    extreme_threshold = float(quantiles[0.999]) * 3
    extreme_households = int((household_max > extreme_threshold).sum())
    say("**Possible faulty meters**")
    say()
    table(["Signal", "Households"], [
        ["More than 50% of readings are exactly zero", mostly_zero],
        [f"Peak reading above {extreme_threshold:.2f} kWh (3 x the 99.9th percentile)", extreme_households],
    ])

    clock_change_days = {"2013-03-31 (clocks forward)": "2013-03-31", "2013-10-27 (clocks back)": "2013-10-27"}
    rows = []
    for label, day in clock_change_days.items():
        on_day = keys[timestamp.dt.strftime("%Y-%m-%d").to_numpy() == day]
        per_house = on_day.drop_duplicates().groupby("LCLid").size()
        rows.append([label, int(per_house.median()), int(per_house.min()), int(per_house.max())])
    say("**Daylight-saving days** (a normal day has 48 readings per household)")
    say()
    table(["Day", "Median readings", "Min", "Max"], rows)


# ---------------------------------------------------------------- London side tables
def profile_london_side_tables():
    say("## 2. London households (the 500 sampled)")
    say()
    households = pd.read_csv(INTERIM / "sampled_households.csv")
    say(f"File: `data/interim/sampled_households.csv`. Rows: {len(households):,}. "
        f"Duplicate household ids: {int(households['LCLid'].duplicated().sum())}.")
    say()
    profile_columns(households)

    coverage = pd.read_csv(INTERIM / "household_coverage_2013.csv")
    say("**Sampling frame (all 5,566 households)**")
    say()
    counts = coverage["eligible"].value_counts()
    table(["Eligible for sampling", "Households"], [[k, v] for k, v in counts.items()])

    say("## 3. London hourly weather")
    say()
    weather = pd.read_csv(RAW / "london" / "weather_hourly_darksky.csv", parse_dates=["time"])
    weather_2013 = weather[weather["time"].dt.year == 2013]
    expected_hours = 365 * 24
    say(f"File: `data/raw/london/weather_hourly_darksky.csv`. Rows: {len(weather):,} "
        f"({len(weather_2013):,} in 2013, expected {expected_hours:,}). "
        f"Duplicate timestamps: {int(weather['time'].duplicated().sum())}. "
        f"Rows sorted by time: {'yes' if weather['time'].is_monotonic_increasing else 'no'}.")
    say()
    profile_columns(weather)


# ---------------------------------------------------------------- Inverter logs
def profile_inverters():
    say("## 4. Inverter logs (two solar plants, India)")
    say()
    date_formats = {"Plant_1": "%d-%m-%Y %H:%M", "Plant_2": "%Y-%m-%d %H:%M:%S"}
    for plant, date_format in date_formats.items():
        generation = pd.read_csv(RAW / "inverters" / f"{plant}_Generation_Data.csv")
        raw_example = generation["DATE_TIME"].iloc[0]
        generation["DATE_TIME"] = pd.to_datetime(generation["DATE_TIME"], format=date_format)

        inverters = generation["SOURCE_KEY"].nunique()
        days = generation["DATE_TIME"].dt.normalize().nunique()
        expected_rows = inverters * days * 96          # one row per 15 minutes
        duplicate_keys = int(generation.duplicated(["DATE_TIME", "SOURCE_KEY"]).sum())
        is_daytime = generation["DATE_TIME"].dt.hour.between(9, 15)
        is_zero_output = generation["AC_POWER"] == 0
        daytime_zero = int((is_daytime & is_zero_output).sum())
        ratio = (generation["DC_POWER"] / generation["AC_POWER"]).replace([np.inf, -np.inf], np.nan)

        say(f"### {plant} generation")
        say()
        say(f"File: `data/raw/inverters/{plant}_Generation_Data.csv`")
        say()
        table(["Measure", "Value"], [
            ["Rows", f"{len(generation):,}"],
            ["Timestamp format in the file", f"`{raw_example}`"],
            ["Inverters", inverters],
            ["Days covered", f"{days} ({generation['DATE_TIME'].min()} to {generation['DATE_TIME'].max()})"],
            ["Expected rows (inverters x days x 96)", f"{expected_rows:,}"],
            ["Missing 15-minute rows", f"{expected_rows - len(generation) + duplicate_keys:,} ({percent(expected_rows - len(generation) + duplicate_keys, expected_rows)})"],
            ["Duplicate (time, inverter) rows", duplicate_keys],
            ["Zero AC output between 09:00 and 15:59", f"{daytime_zero:,} ({percent(daytime_zero, int(is_daytime.sum()))} of daytime rows)"],
            ["Median DC_POWER / AC_POWER ratio", f"{ratio.median():.2f}"],
        ])
        profile_columns(generation)

        sensors = pd.read_csv(RAW / "inverters" / f"{plant}_Weather_Sensor_Data.csv")
        sensors["DATE_TIME"] = pd.to_datetime(sensors["DATE_TIME"])
        say(f"### {plant} weather sensors")
        say()
        say(f"File: `data/raw/inverters/{plant}_Weather_Sensor_Data.csv`. Rows: {len(sensors):,} "
            f"(expected {days * 96:,}). Duplicate timestamps: {int(sensors['DATE_TIME'].duplicated().sum())}.")
        say()
        profile_columns(sensors)


# ---------------------------------------------------------------- PVGIS
def profile_pvgis():
    say("## 5. PVGIS modelled solar output (nested JSON)")
    say()
    for city in ["london", "beirut"]:
        path = RAW / "pvgis" / f"pvgis_{city}_2013.json"
        payload = json.loads(path.read_text())
        hourly = pd.DataFrame(payload["outputs"]["hourly"])
        raw_example = hourly["time"].iloc[0]
        hourly["time"] = pd.to_datetime(hourly["time"], format="%Y%m%d:%H%M")
        location = payload["inputs"]["location"]

        say(f"### {city.title()}")
        say()
        say(f"File: `data/raw/pvgis/pvgis_{city}_2013.json`")
        say()
        table(["Measure", "Value"], [
            ["Top-level keys", ", ".join(payload.keys())],
            ["Location", f"lat {location['latitude']}, lon {location['longitude']}, elevation {location['elevation']} m"],
            ["Hourly records", f"{len(hourly):,} (expected {365 * 24:,})"],
            ["Timestamp format in the file", f"`{raw_example}` (UTC, at minute {hourly['time'].dt.minute.iloc[0]} of each hour)"],
            ["Duplicate timestamps", int(hourly["time"].duplicated().sum())],
            ["Power unit", "W for a 1 kWp system (column `P`)"],
            ["Annual output", f"{hourly['P'].sum() / 1000:,.0f} kWh per kWp"],
        ])
        profile_columns(hourly)


def main():
    say("# Data profile (before cleaning)")
    say()
    say("Generated by `cleaning/02_profile_data.py`. Every number describes the raw data; "
        "nothing has been cleaned yet.")
    say()
    profile_london_readings()
    profile_london_side_tables()
    profile_inverters()
    profile_pvgis()
    REPORT_PATH.write_text("\n".join(report_lines), encoding="utf-8")
    print(f"Wrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
