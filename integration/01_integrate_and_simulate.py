"""Step 4 - Link PostgreSQL and MongoDB, then simulate rooftop solar and batteries.

What it does:
  1. Reads hourly household demand from PostgreSQL (table reading_hourly).
  2. Reads hourly solar output and inverter fault statistics from MongoDB
     (collection solar_daily, using $unwind aggregation pipelines).
  3. Links the two on the shared key: location and hour.
  4. Simulates every household under a set of what-if scenarios
     (city x real-world performance x panel size x battery size).
  5. Writes the results back to PostgreSQL for Power BI, and CSV copies to data/clean/.

Run from the project root (both databases running, data loaded):
    pip install psycopg2-binary pymongo
    python integration/01_integrate_and_simulate.py

Outputs:
    PostgreSQL tables  solar_profile, sim_scenario, sim_result
    data/clean/        sim_scenario.csv, sim_result.csv, solar_profile.csv
    integration/       combined_profile.csv, findings.md

Simulation rules (kept deliberately simple so they can be explained):
  - Each hour, solar is used in the home first. Any surplus charges the battery; any
    surplus left after that is exported.
  - When solar is not enough, the battery discharges before electricity is bought.
  - The battery loses 5% charging and 5% discharging, starts the year empty, and can
    move at most half its capacity in one hour.
  - Hours with no meter reading are skipped (nothing is counted for them).
"""

import io
import os
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CLEAN = PROJECT_ROOT / "data" / "clean"
OUTPUT_DIR = PROJECT_ROOT / "integration"
SIMULATION_SCHEMA = PROJECT_ROOT / "sql" / "simulation_schema.sql"

HOURS_IN_YEAR = 365 * 24
YEAR_START = pd.Timestamp("2013-01-01 00:00:00")
SEASONS = ["Winter", "Spring", "Summer", "Autumn"]
SEASON_OF_MONTH = {12: "Winter", 1: "Winter", 2: "Winter", 3: "Spring", 4: "Spring", 5: "Spring",
                   6: "Summer", 7: "Summer", 8: "Summer", 9: "Autumn", 10: "Autumn", 11: "Autumn"}

PANEL_SIZES_KWP = [1.0, 2.0, 3.0, 4.0]
BATTERY_SIZES_KWH = [0.0, 2.5, 5.0, 10.0]
CHARGE_EFFICIENCY = 0.95
DISCHARGE_EFFICIENCY = 0.95
BATTERY_POWER_SHARE = 0.5            # share of capacity that can move in one hour
BEIRUT_CLOCK_OFFSET_HOURS = 2        # Beirut clock time is 2 hours ahead of London's


# ====================================================================== connections
def read_env_file():
    settings = {}
    for line in (PROJECT_ROOT / ".env").read_text().splitlines():
        is_setting = "=" in line and not line.strip().startswith("#")
        if is_setting:
            key, value = line.split("=", 1)
            settings[key.strip()] = value.strip()
    return settings


def connect_postgres(settings):
    import psycopg2
    return psycopg2.connect(
        host="localhost", port=int(settings.get("POSTGRES_PORT", 5432)),
        user=settings["POSTGRES_USER"], password=settings["POSTGRES_PASSWORD"],
        dbname=settings["POSTGRES_DB"])


def connect_mongo(settings):
    from pymongo import MongoClient
    client = MongoClient(
        host="localhost", port=int(settings.get("MONGO_PORT", 27018)),
        username=settings["MONGO_INITDB_ROOT_USERNAME"],
        password=settings["MONGO_INITDB_ROOT_PASSWORD"], authSource="admin")
    return client["solar_equity"]


# ====================================================================== 1. PostgreSQL
def query_to_frame(connection, sql):
    """Run a SELECT and return a DataFrame (COPY is much faster than fetching rows)."""
    buffer = io.StringIO()
    with connection.cursor() as cursor:
        cursor.copy_expert(f"COPY ({sql}) TO STDOUT WITH (FORMAT csv, HEADER true)", buffer)
    buffer.seek(0)
    return pd.read_csv(buffer)


def load_demand(connection):
    """Hourly demand as a matrix: one row per household, one column per hour of 2013."""
    households = query_to_frame(connection, """
        SELECT h.household_id, c.category_name, h.acorn_code, t.tariff_code
        FROM household h
        JOIN acorn_group g    ON g.acorn_code  = h.acorn_code
        JOIN acorn_category c ON c.category_id = g.category_id
        JOIN tariff t         ON t.tariff_id   = h.tariff_id
        ORDER BY h.household_id""")

    readings = query_to_frame(connection, """
        SELECT household_id, reading_hour, energy_kwh
        FROM reading_hourly""")
    reading_hour = pd.to_datetime(readings["reading_hour"])
    seconds_into_year = (reading_hour - YEAR_START).dt.total_seconds()
    hour_index = (seconds_into_year // 3600).astype("int64").to_numpy()

    households = households[households["household_id"].isin(readings["household_id"].unique())]
    households = households.reset_index(drop=True)
    row_of_household = {household_id: row for row, household_id in enumerate(households["household_id"])}
    row_index = readings["household_id"].map(row_of_household).to_numpy()

    demand = np.full((len(households), HOURS_IN_YEAR), np.nan, dtype=np.float32)
    demand[row_index, hour_index] = readings["energy_kwh"].to_numpy(dtype=np.float32)
    print(f"PostgreSQL: {len(readings):,} hourly readings for {len(households)} households")
    return households, demand


# ====================================================================== 2. MongoDB
def load_solar(database):
    """Hourly modelled output (kWh per kWp) per city, as arrays of 8,760 UTC hours."""
    pipeline = [
        {"$match": {"kind": "modelled"}},
        {"$unwind": "$readings"},
        {"$project": {"_id": 0, "city": 1, "ts": "$readings.ts",
                      "pv_kwh_per_kwp": "$readings.pv_kwh_per_kwp"}},
    ]
    rows = pd.DataFrame(list(database["solar_daily"].aggregate(pipeline)))
    solar = {}
    for city, city_rows in rows.groupby("city"):
        seconds_into_year = (pd.to_datetime(city_rows["ts"]) - YEAR_START).dt.total_seconds()
        hour_index = (seconds_into_year // 3600).astype("int64").to_numpy()
        output = np.zeros(HOURS_IN_YEAR, dtype=np.float64)
        output[hour_index] = city_rows["pv_kwh_per_kwp"].to_numpy()
        solar[city] = output
        print(f"MongoDB: {len(city_rows):,} hourly solar readings for {city} "
              f"({output.sum():,.0f} kWh per kWp per year)")
    return solar


def load_performance_factors(database):
    """How much of the sunlight inverters actually turned into power.

    For each plant: sunlight received during normal readings divided by all sunlight
    received (normal + fault readings). A fault in strong sun therefore costs more
    than a fault in weak sun.
    """
    pipeline = [
        {"$match": {"kind": "inverter"}},
        {"$unwind": "$readings"},
        {"$match": {"readings.irradiation": {"$gt": 0.1}}},
        {"$group": {"_id": {"plant": "$plant", "is_fault": "$readings.is_fault"},
                    "readings": {"$sum": 1},
                    "irradiation_sum": {"$sum": "$readings.irradiation"}}},
    ]
    stats = pd.DataFrame([{"plant": row["_id"]["plant"], "is_fault": row["_id"]["is_fault"],
                           "readings": row["readings"], "irradiation_sum": row["irradiation_sum"]}
                          for row in database["solar_daily"].aggregate(pipeline)])

    def availability(frame):
        normal = frame.loc[~frame["is_fault"], "irradiation_sum"].sum()
        fault = frame.loc[frame["is_fault"], "irradiation_sum"].sum()
        return normal / (normal + fault)

    per_plant = {plant: float(availability(frame)) for plant, frame in stats.groupby("plant")}
    factors = {
        "Modelled": 1.0,
        "Real, average": round(float(availability(stats)), 3),
        "Real, worst plant": round(float(min(per_plant.values())), 3),
    }
    for plant, value in sorted(per_plant.items()):
        print(f"MongoDB: {plant} turned {100 * value:.1f}% of its sunlight into power")
    return factors, per_plant


# ====================================================================== 3. link on hour
def hour_calendar():
    """For every UTC hour of 2013: the London clock hour and the season."""
    utc_hours = pd.date_range(YEAR_START, periods=HOURS_IN_YEAR, freq="h", tz="UTC")
    london_hours = utc_hours.tz_convert("Europe/London")
    season = np.array([SEASON_OF_MONTH[month] for month in london_hours.month])
    return np.asarray(london_hours.hour), season


def solar_on_household_clock(solar, city):
    """Solar output lined up with the household's clock.

    London: demand and solar are both in UTC, so the hours match directly.
    Beirut scenario: the same households are imagined in Beirut, so their clock time
    must line up with Beirut clock time, which is 2 hours ahead of London's.
    """
    if city == "beirut":
        return np.roll(solar[city], BEIRUT_CLOCK_OFFSET_HOURS)
    return solar[city]


# ====================================================================== 4. simulation
def simulate(demand, solar_per_kwp, panel_kwp, battery_kwh):
    """Return hourly matrices: solar used at home, bought from the grid, exported."""
    has_reading = ~np.isnan(demand)
    need = np.where(has_reading, demand, 0.0)
    generation = np.where(has_reading, solar_per_kwp * panel_kwp, 0.0)

    used_directly = np.minimum(need, generation)
    surplus = generation - used_directly
    shortfall = need - used_directly

    from_battery = np.zeros_like(need)
    into_battery = np.zeros_like(need)
    if battery_kwh > 0:
        max_flow = battery_kwh * BATTERY_POWER_SHARE
        stored = np.zeros(need.shape[0])
        for hour in range(need.shape[1]):
            room = (battery_kwh - stored) / CHARGE_EFFICIENCY
            charge = np.minimum(np.minimum(surplus[:, hour], max_flow), room)
            stored += charge * CHARGE_EFFICIENCY

            available = np.minimum(stored * DISCHARGE_EFFICIENCY, max_flow)
            discharge = np.minimum(shortfall[:, hour], available)
            stored -= discharge / DISCHARGE_EFFICIENCY

            into_battery[:, hour] = charge
            from_battery[:, hour] = discharge

    self_consumed = used_directly + from_battery
    grid_import = shortfall - from_battery
    export = surplus - into_battery
    return need, generation, self_consumed, grid_import, export


def run_scenarios(households, demand, solar, factors, season):
    scenarios, results = [], []
    season_masks = {name: season == name for name in SEASONS}
    scenario_id = 0
    for city in ["london", "beirut"]:
        city_solar = solar_on_household_clock(solar, city)
        for performance, factor in factors.items():
            for panel_kwp in PANEL_SIZES_KWP:
                for battery_kwh in BATTERY_SIZES_KWH:
                    scenario_id += 1
                    scenarios.append({"scenario_id": scenario_id, "city": city,
                                      "performance": performance, "performance_factor": factor,
                                      "panel_kwp": panel_kwp, "battery_kwh": battery_kwh})
                    need, generation, self_consumed, grid_import, export = simulate(
                        demand, city_solar * factor, panel_kwp, battery_kwh)
                    for season_name, mask in season_masks.items():
                        results.append(pd.DataFrame({
                            "scenario_id": scenario_id,
                            "household_id": households["household_id"],
                            "season": season_name,
                            "demand_kwh": need[:, mask].sum(axis=1),
                            "solar_kwh": generation[:, mask].sum(axis=1),
                            "self_consumed_kwh": self_consumed[:, mask].sum(axis=1),
                            "grid_import_kwh": grid_import[:, mask].sum(axis=1),
                            "export_kwh": export[:, mask].sum(axis=1),
                        }))
    scenario_table = pd.DataFrame(scenarios)
    result_table = pd.concat(results, ignore_index=True)
    numeric_columns = ["demand_kwh", "solar_kwh", "self_consumed_kwh", "grid_import_kwh", "export_kwh"]
    result_table[numeric_columns] = result_table[numeric_columns].clip(lower=0).round(3)
    print(f"Simulated {len(scenario_table)} scenarios x {len(households)} households "
          f"= {len(result_table):,} result rows")
    return scenario_table, result_table


# ====================================================================== 5. outputs
def build_profiles(households, demand, solar, clock_hour, season):
    """The average day: demand per category (PostgreSQL) next to solar (MongoDB)."""
    calendar = pd.DataFrame({"season": season, "clock_hour": clock_hour})

    solar_rows = []
    for city in ["london", "beirut"]:
        frame = calendar.assign(kwh_per_kwp=solar_on_household_clock(solar, city))
        averages = frame.groupby(["season", "clock_hour"], as_index=False)["kwh_per_kwp"].mean()
        solar_rows.append(averages.assign(city=city))
    solar_profile = pd.concat(solar_rows, ignore_index=True)[["city", "season", "clock_hour", "kwh_per_kwp"]]
    solar_profile["kwh_per_kwp"] = solar_profile["kwh_per_kwp"].round(5)

    demand_rows = []
    for category, group in households.groupby("category_name"):
        category_mean = np.nanmean(demand[group.index.to_numpy()], axis=0)
        frame = calendar.assign(demand_kwh=category_mean)
        averages = frame.groupby(["season", "clock_hour"], as_index=False)["demand_kwh"].mean()
        demand_rows.append(averages.assign(category_name=category))
    demand_profile = pd.concat(demand_rows, ignore_index=True)

    london_solar = solar_profile[solar_profile["city"] == "london"].rename(
        columns={"kwh_per_kwp": "london_solar_kwh_per_kwp"}).drop(columns="city")
    beirut_solar = solar_profile[solar_profile["city"] == "beirut"].rename(
        columns={"kwh_per_kwp": "beirut_solar_kwh_per_kwp"}).drop(columns="city")
    combined = (demand_profile.merge(london_solar, on=["season", "clock_hour"])
                              .merge(beirut_solar, on=["season", "clock_hour"]))
    combined = combined[["category_name", "season", "clock_hour", "demand_kwh",
                         "london_solar_kwh_per_kwp", "beirut_solar_kwh_per_kwp"]].round(5)
    return solar_profile, combined


def copy_frame(connection, frame, table):
    buffer = io.StringIO()
    frame.to_csv(buffer, index=False, header=False)
    buffer.seek(0)
    columns = ", ".join(frame.columns)
    with connection.cursor() as cursor:
        cursor.copy_expert(f"COPY {table} ({columns}) FROM STDIN WITH (FORMAT csv)", buffer)


def save_to_postgres(connection, solar_profile, scenario_table, result_table):
    with connection.cursor() as cursor:
        cursor.execute(SIMULATION_SCHEMA.read_text())
    copy_frame(connection, solar_profile, "solar_profile")
    copy_frame(connection, scenario_table, "sim_scenario")
    copy_frame(connection, result_table, "sim_result")
    connection.commit()
    print("PostgreSQL: wrote solar_profile, sim_scenario and sim_result")


def summarise(households, scenario_table, result_table):
    """Yearly outcome per scenario and category (used for the findings and Power BI checks)."""
    yearly = (result_table.groupby(["scenario_id", "household_id"], as_index=False)
              [["demand_kwh", "solar_kwh", "self_consumed_kwh", "grid_import_kwh", "export_kwh"]].sum())
    yearly = yearly.merge(households[["household_id", "category_name"]], on="household_id")
    by_category = (yearly.groupby(["scenario_id", "category_name"], as_index=False)
                   [["demand_kwh", "solar_kwh", "self_consumed_kwh", "grid_import_kwh", "export_kwh"]].sum())
    by_category["coverage_pct"] = 100 * by_category["self_consumed_kwh"] / by_category["demand_kwh"]
    by_category["self_use_pct"] = 100 * by_category["self_consumed_kwh"] / by_category["solar_kwh"]
    household_counts = households.groupby("category_name").size().rename("households").reset_index()
    by_category = by_category.merge(household_counts, on="category_name")
    by_category["saved_kwh_per_household"] = by_category["self_consumed_kwh"] / by_category["households"]
    return by_category.merge(scenario_table, on="scenario_id")


def write_findings(summary, factors, per_plant, result_table, scenario_table):
    def pick(city, performance, panel_kwp, battery_kwh):
        rows = summary[(summary["city"] == city) & (summary["performance"] == performance)
                       & (summary["panel_kwp"] == panel_kwp) & (summary["battery_kwh"] == battery_kwh)]
        return rows.set_index("category_name")

    def table(rows, title):
        lines = [f"### {title}", "",
                 "| Category | Demand covered by solar | Solar used at home | kWh saved per household |",
                 "| --- | --- | --- | --- |"]
        for category in ["Adversity", "Comfortable", "Affluent"]:
            if category in rows.index:
                row = rows.loc[category]
                lines.append(f"| {category} | {row['coverage_pct']:.1f}% | {row['self_use_pct']:.1f}% "
                             f"| {row['saved_kwh_per_household']:,.0f} |")
        return lines + [""]

    lines = ["# Integration findings", "",
             "Generated by `integration/01_integrate_and_simulate.py`. Household demand comes from "
             "PostgreSQL, solar output and inverter faults from MongoDB; they are linked on location and hour.", "",
             "## Real-world performance (sub-question 4)", "",
             "| Source | Share of sunlight turned into power |", "| --- | --- |"]
    for plant, value in sorted(per_plant.items()):
        lines.append(f"| {plant.replace('_', ' ').title()} | {100 * value:.1f}% |")
    for label, value in factors.items():
        lines.append(f"| Scenario setting: {label} | {100 * value:.1f}% |")
    lines += ["", "## Who benefits (3 kWp system)", ""]
    lines += table(pick("london", "Modelled", 3.0, 0.0), "London, no battery, modelled output")
    lines += table(pick("london", "Real, average", 3.0, 0.0), "London, no battery, real-world average output")
    lines += table(pick("london", "Modelled", 3.0, 5.0), "London, 5 kWh battery, modelled output")
    lines += table(pick("beirut", "Modelled", 3.0, 0.0), "Beirut scenario, no battery, modelled output")

    seasonal = result_table.merge(scenario_table, on="scenario_id")
    base = seasonal[(seasonal["city"] == "london") & (seasonal["performance"] == "Modelled")
                    & (seasonal["panel_kwp"] == 3.0) & (seasonal["battery_kwh"] == 0.0)]
    by_season = base.groupby("season")[["demand_kwh", "self_consumed_kwh"]].sum()
    lines += ["## By season (London, 3 kWp, no battery, all households)", "",
              "| Season | Demand covered by solar |", "| --- | --- |"]
    for season_name in SEASONS:
        row = by_season.loc[season_name]
        lines.append(f"| {season_name} | {100 * row['self_consumed_kwh'] / row['demand_kwh']:.1f}% |")
    lines.append("")
    (OUTPUT_DIR / "findings.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {OUTPUT_DIR / 'findings.md'}")


def run(postgres, mongo_database, write_files=True):
    households, demand = load_demand(postgres)
    solar = load_solar(mongo_database)
    factors, per_plant = load_performance_factors(mongo_database)
    clock_hour, season = hour_calendar()

    solar_profile, combined_profile = build_profiles(households, demand, solar, clock_hour, season)
    scenario_table, result_table = run_scenarios(households, demand, solar, factors, season)
    save_to_postgres(postgres, solar_profile, scenario_table, result_table)
    summary = summarise(households, scenario_table, result_table)

    if write_files:
        OUTPUT_DIR.mkdir(exist_ok=True)
        scenario_table.to_csv(CLEAN / "sim_scenario.csv", index=False)
        result_table.to_csv(CLEAN / "sim_result.csv", index=False)
        solar_profile.to_csv(CLEAN / "solar_profile.csv", index=False)
        combined_profile.to_csv(OUTPUT_DIR / "combined_profile.csv", index=False)
        write_findings(summary, factors, per_plant, result_table, scenario_table)
    return households, demand, solar, factors, scenario_table, result_table, summary, combined_profile


def main():
    settings = read_env_file()
    settings.update({key: os.environ[key] for key in ("POSTGRES_PORT", "MONGO_PORT") if key in os.environ})
    postgres = connect_postgres(settings)
    mongo_database = connect_mongo(settings)
    run(postgres, mongo_database)
    postgres.close()


if __name__ == "__main__":
    main()
