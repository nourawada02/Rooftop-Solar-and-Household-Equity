"""Step 1.3 - Clean every data source and write the cleaning log.

Raw files are never changed. Each part reads raw (or sampled) data, fixes the problems
found by 02_profile_data.py, writes a cleaned file to data/clean/, and records what it
did: issue found -> action taken -> rows affected -> justification.

Run from the project root (after 01 and 02):
    python cleaning/03_clean_data.py

Outputs:
    data/clean/london_halfhourly_clean.csv   half-hourly readings, gaps filled where short
    data/clean/london_hourly_clean.csv       the same readings summed to hours
    data/clean/households_clean.csv          the 500 households
    data/clean/weather_hourly_clean.csv      London weather for 2013
    data/clean/inverter_readings_clean.csv   both solar plants, one format, faults flagged
    data/clean/pvgis_hourly_clean.csv        modelled solar output, London and Beirut
    cleaning/cleaning_log.md                 the cleaning log with before/after evidence
    cleaning/figures/*.png                   before/after charts

You can run one part at a time with --only (london, weather, inverters, pvgis, log).
"""

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW = PROJECT_ROOT / "data" / "raw"
INTERIM = PROJECT_ROOT / "data" / "interim"
CLEAN = PROJECT_ROOT / "data" / "clean"
FIGURES = PROJECT_ROOT / "cleaning" / "figures"
LOG_PATH = PROJECT_ROOT / "cleaning" / "cleaning_log.md"

YEAR_START = pd.Timestamp("2013-01-01 00:00:00")
HALF_HOURS_IN_YEAR = 365 * 48
MAX_GAP_TO_FILL = 4            # half-hours, i.e. gaps of up to 2 hours are interpolated
FAULT_MIN_IRRADIATION = 0.1    # sensor reading above which the sun is clearly up
PLANT_1_DC_SCALE = 10          # Plant 1 reports DC power 10x too high

# Chart style: one blue and one orange, neutral text, light surface.
BLUE, ORANGE = "#2a78d6", "#eb6834"
TEXT, MUTED, SURFACE, GRID = "#0b0b0b", "#52514e", "#fcfcfb", "#e4e3df"
plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": GRID, "axes.labelcolor": MUTED, "text.color": TEXT,
    "xtick.color": MUTED, "ytick.color": MUTED, "axes.grid": True, "grid.color": GRID,
    "axes.spines.top": False, "axes.spines.right": False, "font.size": 10,
    "axes.titlesize": 11, "axes.titleweight": "bold", "axes.titlelocation": "left",
})


def save_part(name, entries, evidence):
    """Each part stores its log entries so the log can be assembled at the end."""
    INTERIM.mkdir(parents=True, exist_ok=True)
    payload = {"entries": entries, "evidence": evidence}
    (INTERIM / f"cleaning_part_{name}.json").write_text(json.dumps(payload, indent=2))


def entry(dataset, issue, action, rows_affected, justification):
    return {"dataset": dataset, "issue": issue, "action": action,
            "rows": rows_affected, "why": justification}


# =============================================================== London readings
LONDON_CACHE = INTERIM / "london_cleaning_cache.npz"


def clean_london():
    """Runs in three stages; the first saves its result so the others can reuse it."""
    london_load_and_fill()
    london_write_half_hourly()
    london_write_hourly_and_log()


def london_load_and_fill():
    households = pd.read_csv(INTERIM / "sampled_households.csv")
    household_ids = households["LCLid"].tolist()
    row_of_household = {household_id: row for row, household_id in enumerate(household_ids)}
    household_count = len(household_ids)

    readings = np.full((household_count, HALF_HOURS_IN_YEAR), np.nan, dtype=np.float32)
    times_seen = np.zeros((household_count, HALF_HOURS_IN_YEAR), dtype=np.uint8)
    raw_rows = 0
    rows_with_spaces = 0
    rows_not_numeric = 0

    path = INTERIM / "london_halfhourly_sample.csv"
    for chunk in pd.read_csv(path, dtype=str, chunksize=1_000_000):
        chunk.columns = ["LCLid", "tstp", "energy_raw"]
        raw_rows += len(chunk)

        energy_text = chunk["energy_raw"].str.strip()
        rows_with_spaces += int((energy_text != chunk["energy_raw"]).sum())
        energy = pd.to_numeric(energy_text, errors="coerce").to_numpy(dtype=np.float32)
        rows_not_numeric += int(np.isnan(energy).sum())

        timestamp = pd.to_datetime(chunk["tstp"].str.slice(0, 19), format="%Y-%m-%d %H:%M:%S")
        seconds_into_year = (timestamp - YEAR_START).dt.total_seconds()
        slot = (seconds_into_year // 1800).astype("int64").to_numpy()   # 1800 s = 30 min
        row = chunk["LCLid"].map(row_of_household).to_numpy()

        np.add.at(times_seen, (row, slot), 1)
        readings[row, slot] = energy

    duplicate_rows = int((times_seen.astype(np.int64) - 1).clip(min=0).sum())
    missing_before = int(np.isnan(readings).sum())
    before_stats = describe(readings)
    readings_before = readings.copy()

    # Fill short gaps by straight-line interpolation between the neighbours.
    was_filled = np.zeros_like(readings, dtype=bool)
    short_gaps = 0
    long_gaps = 0
    for row in range(household_count):
        values = readings[row]
        is_missing = np.isnan(values)
        if not is_missing.any():
            continue
        edges = np.diff(np.concatenate(([0], is_missing.astype(np.int8), [0])))
        gap_starts = np.where(edges == 1)[0]
        gap_ends = np.where(edges == -1)[0]            # first slot after the gap
        for start, end in zip(gap_starts, gap_ends):
            gap_length = end - start
            has_neighbours = start > 0 and end < HALF_HOURS_IN_YEAR
            is_short = gap_length <= MAX_GAP_TO_FILL
            if is_short and has_neighbours:
                values[start:end] = np.interp(
                    np.arange(start, end), [start - 1, end], [values[start - 1], values[end]])
                was_filled[row, start:end] = True
                short_gaps += 1
            else:
                long_gaps += 1

    counts = {
        "raw_rows": raw_rows, "rows_with_spaces": rows_with_spaces,
        "rows_not_numeric": rows_not_numeric, "duplicate_rows": duplicate_rows,
        "missing_before": missing_before, "missing_after": int(np.isnan(readings).sum()),
        "filled_count": int(was_filled.sum()), "short_gaps": short_gaps, "long_gaps": long_gaps,
        "before_stats": before_stats, "after_stats": describe(readings),
    }
    np.savez(LONDON_CACHE, readings=readings, was_filled=was_filled, readings_before=readings_before)
    (INTERIM / "london_cleaning_counts.json").write_text(json.dumps(counts, indent=2))
    chart_gap_fill(readings_before, readings, was_filled, household_ids)
    print(f"London: loaded {raw_rows:,} rows, filled {counts['filled_count']:,}, "
          f"still missing {counts['missing_after']:,}")


def slot_labels():
    offsets = pd.to_timedelta(np.arange(HALF_HOURS_IN_YEAR) * 30, unit="min")
    return np.array((YEAR_START + offsets).strftime("%Y-%m-%d %H:%M:%S"))


def london_write_half_hourly():
    household_ids = pd.read_csv(INTERIM / "sampled_households.csv")["LCLid"].tolist()
    cache = np.load(LONDON_CACHE)
    readings, was_filled = cache["readings"], cache["was_filled"]

    row_index, slot_index = np.nonzero(~np.isnan(readings))
    half_hourly = pd.DataFrame({
        "household_id": pd.Categorical.from_codes(row_index, categories=household_ids),
        "reading_ts_utc": slot_labels()[slot_index],
        "energy_kwh": np.round(readings[row_index, slot_index].astype(np.float64), 4),
        "is_imputed": was_filled[row_index, slot_index],
    })
    half_hourly.to_csv(CLEAN / "london_halfhourly_clean.csv", index=False)
    print(f"London: wrote {len(half_hourly):,} half-hourly rows")


def london_write_hourly_and_log():
    households = pd.read_csv(INTERIM / "sampled_households.csv")
    household_ids = households["LCLid"].tolist()
    household_count = len(household_ids)
    cache = np.load(LONDON_CACHE)
    readings, was_filled = cache["readings"], cache["was_filled"]
    counts = json.loads((INTERIM / "london_cleaning_counts.json").read_text())
    half_hourly_rows = int((~np.isnan(readings)).sum())

    # An hour is kept only if both of its half-hours are present.
    pairs = readings.reshape(household_count, HALF_HOURS_IN_YEAR // 2, 2)
    hourly = pairs.sum(axis=2)                          # NaN if either half is missing
    hourly_filled = was_filled.reshape(household_count, HALF_HOURS_IN_YEAR // 2, 2).any(axis=2)
    row_index, hour_index = np.nonzero(~np.isnan(hourly))
    hour_labels = slot_labels()[::2]
    hourly_table = pd.DataFrame({
        "household_id": pd.Categorical.from_codes(row_index, categories=household_ids),
        "reading_hour_utc": hour_labels[hour_index],
        "energy_kwh": np.round(hourly[row_index, hour_index].astype(np.float64), 4),
        "is_imputed": hourly_filled[row_index, hour_index],
    })
    hourly_table.to_csv(CLEAN / "london_hourly_clean.csv", index=False)
    hourly_rows = len(hourly_table)

    households_clean = households.rename(columns={
        "LCLid": "household_id", "stdorToU": "tariff", "Acorn": "acorn_code",
        "Acorn_grouped": "acorn_category"})[
        ["household_id", "tariff", "acorn_code", "acorn_category"]]
    households_clean.to_csv(CLEAN / "households_clean.csv", index=False)

    frame = pd.read_csv(INTERIM / "household_coverage_2013.csv")
    reasons = frame["eligible"].value_counts()
    entries = [
        entry("London households", "Socio-economic category unknown (ACORN-U or blank)",
              "Excluded from the sampling frame", f"{reasons.get('no: unknown category', 0)} households",
              "The guiding question compares categories, so a household without one cannot be used."),
        entry("London households", "Household has under 90% of the 2013 readings",
              "Excluded from the sampling frame", f"{reasons.get('no: under 90% coverage', 0)} households",
              "Homes that joined or left the trial mid-year would distort seasonal and yearly totals."),
        entry("London households", "Meter reads zero most of the time (median reading is zero on over half of the days)",
              "Excluded from the sampling frame", f"{reasons.get('no: mostly zero readings', 0)} households",
              "Probably an empty home or a faulty meter; it does not represent a lived-in household."),
        entry("London readings", "Dataset too large for a laptop database (about 167.8 million rows)",
              "Sampled 500 eligible households (167/167/166 per category, fixed seed) and kept 2013 only",
              f"{counts['raw_rows']:,} rows kept", "2013 is the only full calendar year and matches the PVGIS solar year."),
        entry("London readings", "Energy stored as text with surrounding spaces",
              "Stripped spaces and converted to a decimal number", f"{counts['rows_with_spaces']:,} rows",
              "Text cannot be summed or averaged; the value itself is unchanged."),
        entry("London readings", "Non-numeric energy values (`Null`)",
              "Treated as missing", f"{counts['rows_not_numeric']:,} rows", "A reading with no value carries no information."),
        entry("London readings", "Duplicate (household, timestamp) rows",
              "Kept one row per household and timestamp", f"{counts['duplicate_rows']:,} rows",
              "A meter produces one reading per half-hour."),
        entry("London readings", f"Missing half-hours in short gaps (up to {MAX_GAP_TO_FILL} in a row, i.e. 2 hours)",
              "Filled by straight-line interpolation between the neighbouring readings; flagged `is_imputed`",
              f"{counts['filled_count']:,} readings in {counts['short_gaps']:,} gaps",
              "Household demand changes smoothly over two hours, so the estimate is safe, and the flag keeps it traceable."),
        entry("London readings", "Missing half-hours in long gaps (over 2 hours) or at the edges of the year",
              "Left missing (no row written)", f"{counts['missing_after']:,} readings in {counts['long_gaps']:,} gaps",
              "Interpolating across many hours would invent a demand pattern that was never measured."),
        entry("London readings", "Timestamps carry no time zone and do not shift on clock-change days",
              "Labelled as UTC (`reading_ts_utc`)", f"{half_hourly_rows:,} rows",
              "Each household has 48 readings on both clock-change days, so the clock never moved: the data is in GMT."),
        entry("London readings", "Solar data is hourly but meter data is half-hourly",
              "Added an hourly file: the sum of the two half-hours, only when both are present",
              f"{hourly_rows:,} hourly rows", "Demand and solar output must share the same time step to be compared."),
    ]
    evidence = {
        "london_before": {"rows": counts["raw_rows"], "missing": counts["missing_before"], **counts["before_stats"]},
        "london_after": {"rows": half_hourly_rows, "missing": counts["missing_after"], **counts["after_stats"]},
    }
    save_part("london", entries, evidence)
    print(f"London: wrote {hourly_rows:,} hourly rows and the log entries")


def describe(matrix):
    return {"mean": round(float(np.nanmean(matrix)), 4),
            "median": round(float(np.nanmedian(matrix)), 4),
            "max": round(float(np.nanmax(matrix)), 3),
            "valid": int((~np.isnan(matrix)).sum())}


def chart_gap_fill(before, after, was_filled, household_ids):
    """Before/after: one household around its first filled gap."""
    rows_with_fill = np.where(was_filled.any(axis=1))[0]
    if len(rows_with_fill) == 0:
        return
    row = rows_with_fill[0]
    first_filled_slot = int(np.argmax(was_filled[row]))
    window = slice(max(first_filled_slot - 36, 0), first_filled_slot + 40)
    hours = (np.arange(window.start, window.stop) - first_filled_slot) / 2

    figure, axes = plt.subplots(1, 2, figsize=(10, 3.6), sharey=True)
    axes[0].plot(hours, before[row, window], color=BLUE, linewidth=2)
    axes[0].set_title("Before: readings missing")
    axes[1].plot(hours, after[row, window], color=BLUE, linewidth=2, label="Measured")
    filled_here = was_filled[row, window]
    axes[1].scatter(hours[filled_here], after[row, window][filled_here], color=ORANGE, s=42,
                    zorder=3, edgecolor=SURFACE, linewidth=1.5, label="Filled by interpolation")
    axes[1].set_title("After: short gap filled and flagged")
    axes[1].legend(frameon=False, loc="upper right")
    for axis in axes:
        axis.set_xlabel("Hours from the gap")
    axes[0].set_ylabel("Energy per half-hour (kWh)")
    figure.suptitle(f"Household {household_ids[row]}: a short gap in the meter readings",
                    x=0.01, ha="left", fontsize=12, fontweight="bold")
    figure.tight_layout()
    figure.savefig(FIGURES / "01_gap_fill_before_after.png", dpi=150)
    plt.close(figure)


# =============================================================== Weather
def clean_weather():
    weather = pd.read_csv(RAW / "london" / "weather_hourly_darksky.csv", parse_dates=["time"])
    raw_rows = len(weather)
    was_sorted = bool(weather["time"].is_monotonic_increasing)

    weather = weather.sort_values("time")
    in_2013 = weather[weather["time"].dt.year == 2013].set_index("time")
    rows_2013_before = len(in_2013)
    missing_values_before = int(in_2013[["temperature", "humidity", "windSpeed", "pressure", "visibility"]]
                                .isna().sum().sum())

    full_year = pd.date_range("2013-01-01 00:00", "2013-12-31 23:00", freq="h")
    in_2013 = in_2013.reindex(full_year)
    hours_added = len(in_2013) - rows_2013_before

    numeric_columns = ["temperature", "humidity", "windSpeed", "pressure", "visibility"]
    in_2013[numeric_columns] = in_2013[numeric_columns].interpolate(limit=3, limit_direction="both")
    in_2013["summary"] = in_2013["summary"].ffill().bfill()
    missing_values_after = int(in_2013[numeric_columns].isna().sum().sum())

    clean = in_2013[numeric_columns + ["summary"]].rename(columns={
        "temperature": "temperature_c", "windSpeed": "wind_speed", "pressure": "pressure_hpa",
        "visibility": "visibility_km", "summary": "conditions"})
    clean.index.name = "weather_hour"
    clean.round(3).to_csv(CLEAN / "weather_hourly_clean.csv")

    entries = [
        entry("London weather", "Rows are not in time order",
              "Sorted by time", f"{raw_rows:,} rows", "Time-series steps (gap filling, joins) need ordered rows."),
        entry("London weather", "File covers Nov 2011 to Mar 2014",
              "Kept 2013 only", f"{raw_rows - rows_2013_before:,} rows removed", "Matches the year of the meter sample."),
        entry("London weather", "Hours missing from 2013",
              "Inserted the missing hours and interpolated their values", f"{hours_added} rows added",
              "Weather changes smoothly from hour to hour; a complete hourly grid simplifies every join."),
        entry("London weather", "Missing numeric values (mostly pressure)",
              "Interpolated from neighbouring hours (up to 3 in a row)",
              f"{missing_values_before + hours_added * len(numeric_columns) - missing_values_after} values",
              "Short gaps in slowly changing measurements."),
        entry("London weather", "Columns not needed for the question (icon, wind bearing, dew point, apparent temperature, precipitation type)",
              "Dropped", "5 columns", "They do not help explain electricity demand or solar output here."),
    ]
    save_part("weather", entries, {})
    print(f"Weather: {len(clean):,} hourly rows, {hours_added} hours added")


# =============================================================== Inverters
def clean_inverters():
    date_formats = {"Plant_1": "%d-%m-%Y %H:%M", "Plant_2": "%Y-%m-%d %H:%M:%S"}
    cleaned_plants = []
    entries = []
    evidence = {}
    chart_data = {}

    for plant, date_format in date_formats.items():
        generation = pd.read_csv(RAW / "inverters" / f"{plant}_Generation_Data.csv")
        sensors = pd.read_csv(RAW / "inverters" / f"{plant}_Weather_Sensor_Data.csv")
        generation["DATE_TIME"] = pd.to_datetime(generation["DATE_TIME"], format=date_format)
        sensors["DATE_TIME"] = pd.to_datetime(sensors["DATE_TIME"], format="%Y-%m-%d %H:%M:%S")

        inverters = generation["SOURCE_KEY"].nunique()
        days = generation["DATE_TIME"].dt.normalize().nunique()
        expected_rows = inverters * days * 96
        missing_rows = expected_rows - len(generation)

        dc_before = generation["DC_POWER"].copy()
        if plant == "Plant_1":
            generation["DC_POWER"] = generation["DC_POWER"] / PLANT_1_DC_SCALE
            chart_data["plant_1"] = (dc_before, generation["DC_POWER"].copy(), generation["AC_POWER"].copy())
            evidence["plant_1_dc_before"] = {"mean": round(float(dc_before.mean()), 1),
                                             "max": round(float(dc_before.max()), 1)}
            evidence["plant_1_dc_after"] = {"mean": round(float(generation["DC_POWER"].mean()), 1),
                                            "max": round(float(generation["DC_POWER"].max()), 1)}
            evidence["plant_1_ac"] = {"mean": round(float(generation["AC_POWER"].mean()), 1),
                                      "max": round(float(generation["AC_POWER"].max()), 1)}
            entries.append(entry(
                "Inverter logs, Plant 1", "DC power is about 10 times AC power (Plant 2: about equal)",
                f"Divided DC_POWER by {PLANT_1_DC_SCALE}", f"{len(generation):,} rows",
                "An inverter loses a few percent converting DC to AC, not 90%; the column is on a different scale from Plant 2."))

        weather_columns = sensors[["DATE_TIME", "AMBIENT_TEMPERATURE", "MODULE_TEMPERATURE", "IRRADIATION"]]
        merged = generation.merge(weather_columns, on="DATE_TIME", how="left")
        rows_without_sensor = int(merged["IRRADIATION"].isna().sum())

        sun_is_up = merged["IRRADIATION"] > FAULT_MIN_IRRADIATION
        no_output = merged["AC_POWER"] == 0
        merged["is_fault"] = sun_is_up & no_output
        fault_rows = int(merged["is_fault"].sum())
        sunny_rows = int(sun_is_up.sum())

        merged.insert(0, "plant", plant.lower())
        cleaned_plants.append(merged)
        chart_data[plant.lower() + "_faults"] = merged[["IRRADIATION", "AC_POWER", "is_fault"]].copy()
        evidence[plant.lower() + "_faults"] = {"sunny_rows": sunny_rows, "fault_rows": fault_rows}

        entries.append(entry(
            f"Inverter logs, {plant.replace('_', ' ')}", f"Timestamps written as `{generation['DATE_TIME'].dt.strftime(date_format).iloc[0]}`",
            "Parsed and rewritten in one format (YYYY-MM-DD HH:MM:SS)", f"{len(generation):,} rows",
            "The two plants use different date formats; one format is needed to combine them."))
        entries.append(entry(
            f"Inverter logs, {plant.replace('_', ' ')}", "Zero AC output while the sun is clearly up (irradiation above 0.1)",
            "Kept and flagged `is_fault`", f"{fault_rows:,} rows ({100 * fault_rows / sunny_rows:.1f}% of sunlit rows)",
            "These outages are real equipment behaviour and are the evidence for sub-question 4; deleting them would hide the losses."))
        entries.append(entry(
            f"Inverter logs, {plant.replace('_', ' ')}", "15-minute rows missing from the log",
            "Left missing (no rows invented)", f"{missing_rows:,} rows absent of {expected_rows:,} expected",
            "A missing log line may be a communication loss or an outage; we cannot tell which, so we do not guess."))
        if rows_without_sensor:
            entries.append(entry(
                f"Inverter logs, {plant.replace('_', ' ')}", "Generation rows with no weather-sensor reading at that time",
                "Kept with empty sensor columns; not flagged as faults", f"{rows_without_sensor:,} rows",
                "Without irradiation we cannot judge whether zero output is a fault."))

    combined = pd.concat(cleaned_plants, ignore_index=True).rename(columns={
        "DATE_TIME": "reading_ts", "PLANT_ID": "plant_id", "SOURCE_KEY": "inverter_id",
        "DC_POWER": "dc_power_kw", "AC_POWER": "ac_power_kw", "DAILY_YIELD": "daily_yield_kwh",
        "TOTAL_YIELD": "total_yield_kwh", "AMBIENT_TEMPERATURE": "ambient_temp_c",
        "MODULE_TEMPERATURE": "module_temp_c", "IRRADIATION": "irradiation"})
    combined = combined.sort_values(["plant", "inverter_id", "reading_ts"])
    combined.round(4).to_csv(CLEAN / "inverter_readings_clean.csv", index=False,
                             date_format="%Y-%m-%d %H:%M:%S")

    chart_plant_1_scale(*chart_data["plant_1"])
    chart_faults(chart_data["plant_2_faults"])
    save_part("inverters", entries, evidence)
    print(f"Inverters: {len(combined):,} rows, faults flagged: {int(combined['is_fault'].sum()):,}")


def chart_plant_1_scale(dc_before, dc_after, ac_power):
    figure, axes = plt.subplots(1, 2, figsize=(10, 3.8))
    panels = [(dc_before, "Before: DC is about 10 x AC"), (dc_after, "After: DC divided by 10")]
    for axis, (dc_values, title) in zip(axes, panels):
        axis.scatter(ac_power, dc_values, s=4, color=BLUE, alpha=0.25, linewidth=0)
        limit = float(max(ac_power.max(), dc_values.max()))
        axis.plot([0, ac_power.max()], [0, ac_power.max()], color=MUTED, linewidth=1, linestyle="--")
        axis.annotate("DC = AC", xy=(ac_power.max(), ac_power.max()), xytext=(-6, 8),
                      textcoords="offset points", ha="right", color=MUTED, fontsize=9)
        axis.set_xlim(0, ac_power.max() * 1.03)
        axis.set_ylim(0, limit * 1.05)
        axis.set_title(title)
        axis.set_xlabel("AC power (kW)")
    axes[0].set_ylabel("DC power (kW)")
    figure.suptitle("Plant 1: DC power against AC power", x=0.01, ha="left", fontsize=12, fontweight="bold")
    figure.tight_layout()
    figure.savefig(FIGURES / "02_plant1_dc_scale_before_after.png", dpi=150)
    plt.close(figure)


def chart_faults(data):
    data = data.dropna(subset=["IRRADIATION"])
    normal = data[~data["is_fault"]]
    faults = data[data["is_fault"]]
    figure, axes = plt.subplots(1, 2, figsize=(10, 3.8), sharey=True)
    axes[0].scatter(data["IRRADIATION"], data["AC_POWER"], s=4, color=BLUE, alpha=0.25, linewidth=0)
    axes[0].set_title("Before: outages hidden among normal readings")
    axes[1].scatter(normal["IRRADIATION"], normal["AC_POWER"], s=4, color=BLUE, alpha=0.25,
                    linewidth=0, label="Normal reading")
    axes[1].scatter(faults["IRRADIATION"], faults["AC_POWER"], s=10, color=ORANGE, alpha=0.8,
                    linewidth=0, label=f"Flagged fault ({len(faults):,} rows)")
    axes[1].set_title("After: zero output in sunlight is flagged")
    legend = axes[1].legend(frameon=False, loc="upper left", markerscale=2.5)
    for handle in legend.legend_handles:
        handle.set_alpha(1)
    for axis in axes:
        axis.set_xlabel("Irradiation (sensor reading)")
    axes[0].set_ylabel("AC power (kW)")
    figure.suptitle("Plant 2: inverter output against sunlight", x=0.01, ha="left", fontsize=12, fontweight="bold")
    figure.tight_layout()
    figure.savefig(FIGURES / "03_plant2_faults_before_after.png", dpi=150)
    plt.close(figure)


# =============================================================== PVGIS
def clean_pvgis():
    cities = []
    entries = []
    for city in ["london", "beirut"]:
        payload = json.loads((RAW / "pvgis" / f"pvgis_{city}_2013.json").read_text())
        hourly = pd.DataFrame(payload["outputs"]["hourly"])
        raw_example = hourly["time"].iloc[0]
        timestamp = pd.to_datetime(hourly["time"], format="%Y%m%d:%H%M")
        clean = pd.DataFrame({
            "city": city,
            "solar_hour_utc": timestamp.dt.floor("h"),
            "pv_kwh_per_kwp": (hourly["P"] / 1000).round(5),
            "irradiance_w_m2": hourly["G(i)"],
            "sun_height_deg": hourly["H_sun"],
            "air_temp_c": hourly["T2m"],
            "wind_speed_m_s": hourly["WS10m"],
        })
        cities.append(clean)
        entries.append(entry(
            f"PVGIS, {city.title()}", f"Timestamps like `{raw_example}` (compact text, a few minutes past each hour)",
            "Parsed and moved to the start of the hour (`solar_hour_utc`)", f"{len(clean):,} rows",
            "The value is the average for that hour; an on-the-hour key is needed to join with the meter data."))

    combined = pd.concat(cities, ignore_index=True)
    combined.to_csv(CLEAN / "pvgis_hourly_clean.csv", index=False, date_format="%Y-%m-%d %H:%M:%S")
    entries.append(entry(
        "PVGIS, both cities", "Output is average power in W for a 1 kWp system",
        "Converted to energy: kWh per kWp for the hour (W / 1000)", f"{len(combined):,} rows",
        "Meter data is in kWh; one hour at P watts is P/1000 kWh."))
    entries.append(entry(
        "PVGIS, both cities", "Nested JSON (inputs, outputs.hourly, meta) with a constant `Int` column",
        "Flattened the hourly records to a table and dropped `Int`", f"{len(combined):,} rows",
        "A flat copy is needed for the join; the original nested JSON still goes to MongoDB."))
    save_part("pvgis", entries, {})
    print(f"PVGIS: {len(combined):,} rows")


# =============================================================== Log
def write_log():
    order = ["london", "weather", "inverters", "pvgis"]
    parts = {name: json.loads((INTERIM / f"cleaning_part_{name}.json").read_text()) for name in order}
    lines = ["# Cleaning log", "",
             "Generated by `cleaning/03_clean_data.py`. Raw files are never modified; every step "
             "runs on a copy and can be re-run.", "",
             "| # | Dataset | Issue found | Action taken | Rows affected | Justification |",
             "| --- | --- | --- | --- | --- | --- |"]
    number = 0
    for name in order:
        for item in parts[name]["entries"]:
            number += 1
            lines.append(f"| {number} | {item['dataset']} | {item['issue']} | {item['action']} | "
                         f"{item['rows']} | {item['why']} |")

    london = parts["london"]["evidence"]
    plants = parts["inverters"]["evidence"]
    before, after = london["london_before"], london["london_after"]
    lines += ["", "## Before/after evidence", "",
              "### 1. London readings: short gaps filled", "",
              "| | Valid readings | Missing half-hours | Mean (kWh) | Median (kWh) | Max (kWh) |",
              "| --- | --- | --- | --- | --- | --- |",
              f"| Before | {before['valid']:,} | {before['missing']:,} | {before['mean']} | {before['median']} | {before['max']} |",
              f"| After | {after['valid']:,} | {after['missing']:,} | {after['mean']} | {after['median']} | {after['max']} |",
              "", "![Gap fill before and after](figures/01_gap_fill_before_after.png)", "",
              "### 2. Plant 1: DC power rescaled", "",
              "| | Mean DC power | Max DC power | Mean AC power | Max AC power |",
              "| --- | --- | --- | --- | --- |",
              f"| Before | {plants['plant_1_dc_before']['mean']:,} | {plants['plant_1_dc_before']['max']:,} | {plants['plant_1_ac']['mean']:,} | {plants['plant_1_ac']['max']:,} |",
              f"| After | {plants['plant_1_dc_after']['mean']:,} | {plants['plant_1_dc_after']['max']:,} | {plants['plant_1_ac']['mean']:,} | {plants['plant_1_ac']['max']:,} |",
              "", "![Plant 1 DC scale before and after](figures/02_plant1_dc_scale_before_after.png)", "",
              "### 3. Inverter faults flagged", "",
              "| Plant | Sunlit rows | Flagged as fault | Share |",
              "| --- | --- | --- | --- |"]
    for plant in ["plant_1", "plant_2"]:
        counts = plants[plant + "_faults"]
        share = 100 * counts["fault_rows"] / counts["sunny_rows"]
        lines.append(f"| {plant.replace('_', ' ').title()} | {counts['sunny_rows']:,} | {counts['fault_rows']:,} | {share:.1f}% |")
    lines += ["", "![Plant 2 faults before and after](figures/03_plant2_faults_before_after.png)", ""]
    LOG_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {LOG_PATH} with {number} entries")


def main():
    steps = {"london": clean_london, "weather": clean_weather, "inverters": clean_inverters,
             "pvgis": clean_pvgis, "log": write_log}
    london_stages = {"london-1": london_load_and_fill, "london-2": london_write_half_hourly,
                     "london-3": london_write_hourly_and_log}
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--only", choices=list(steps) + list(london_stages),
                        help="run a single part (london-1/2/3 are the three London stages)")
    args = parser.parse_args()
    if args.only in london_stages:
        CLEAN.mkdir(parents=True, exist_ok=True)
        FIGURES.mkdir(parents=True, exist_ok=True)
        london_stages[args.only]()
        return

    CLEAN.mkdir(parents=True, exist_ok=True)
    FIGURES.mkdir(parents=True, exist_ok=True)
    chosen_steps = [args.only] if args.only else list(steps)
    for name in chosen_steps:
        steps[name]()


if __name__ == "__main__":
    main()
