"""Step 1.1 - Draw a balanced sample of households from the London smart-meter data.

Why: the full half-hourly dataset has about 167.8 million rows (7.4 GB), which is too
large for a laptop database and Power BI. We keep 500 households, balanced across the
three socio-economic categories, and only the readings from 2013 (the one full
calendar year, which also matches the PVGIS solar data).

Which households can be chosen (the "sampling frame"):
  1. The household belongs to one of the three known categories. Households whose
     category is unknown (ACORN-U or blank) are left out, because the guiding
     question compares categories.
  2. The household has at least 90% of the possible 2013 readings. A household that
     joined or left the trial mid-year would distort yearly and seasonal totals.

The readings are copied exactly as they are in the raw files. No cleaning happens here.

Run from the project root:
    python cleaning/01_sample_households.py

Outputs (in data/interim/):
    household_coverage_2013.csv    every household, its category and 2013 coverage
    sampled_households.csv         the 500 chosen households and their attributes
    london_halfhourly_sample.csv   their raw 2013 half-hourly readings
"""

import argparse
import csv
import random
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_LONDON = PROJECT_ROOT / "data" / "raw" / "london"
BLOCKS_DIR = RAW_LONDON / "halfhourly_dataset" / "halfhourly_dataset"
INTERIM_DIR = PROJECT_ROOT / "data" / "interim"

RANDOM_SEED = 42                      # fixed so everyone gets the same sample
SAMPLE_YEAR = "2013"
HOUSEHOLDS_PER_CATEGORY = {"Adversity": 167, "Comfortable": 167, "Affluent": 166}
READINGS_IN_FULL_YEAR = 365 * 48      # 17,520 half-hours
MIN_COVERAGE = 0.90
TOTAL_BLOCKS = 112


def count_readings_per_household():
    """Count each household's 2013 readings, using the daily summary file."""
    readings = Counter()
    with open(RAW_LONDON / "daily_dataset.csv", newline="") as f:
        for row in csv.DictReader(f):
            is_sample_year = row["day"].startswith(SAMPLE_YEAR)
            if is_sample_year:
                readings[row["LCLid"]] += int(row["energy_count"] or 0)
    return readings


def build_sampling_frame():
    """Label every household as eligible or not, and say why."""
    with open(RAW_LONDON / "informations_households.csv", newline="") as f:
        all_households = list(csv.DictReader(f))

    readings = count_readings_per_household()
    for household in all_households:
        coverage = readings.get(household["LCLid"], 0) / READINGS_IN_FULL_YEAR
        has_known_category = household["Acorn_grouped"] in HOUSEHOLDS_PER_CATEGORY
        has_enough_data = coverage >= MIN_COVERAGE

        household["readings_2013"] = readings.get(household["LCLid"], 0)
        household["coverage_2013"] = round(coverage, 4)
        if not has_known_category:
            household["eligible"] = "no: unknown category"
        elif not has_enough_data:
            household["eligible"] = "no: under 90% coverage"
        else:
            household["eligible"] = "yes"
    return all_households


def choose_households(all_households):
    """Pick the households, the same ones every run."""
    rng = random.Random(RANDOM_SEED)
    chosen = []
    for category, how_many in HOUSEHOLDS_PER_CATEGORY.items():
        eligible_in_category = [
            h for h in all_households
            if h["Acorn_grouped"] == category and h["eligible"] == "yes"
        ]
        eligible_in_category.sort(key=lambda h: h["LCLid"])   # stable order
        chosen.extend(rng.sample(eligible_in_category, how_many))

    chosen.sort(key=lambda h: h["LCLid"])
    return chosen


def write_csv(rows, output_path):
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return output_path


def copy_readings(chosen, first_block, last_block, output_path, write_header):
    """Stream the block files and keep rows for chosen households in SAMPLE_YEAR."""
    chosen_ids = {h["LCLid"] for h in chosen}
    blocks_needed = {h["file"] for h in chosen}
    rows_read = 0
    rows_kept = 0

    with open(output_path, "w", newline="") as out:
        if write_header:
            out.write("LCLid,tstp,energy(kWh/hh)\n")

        for block_number in range(first_block, last_block + 1):
            block_name = f"block_{block_number}"
            if block_name not in blocks_needed:
                continue

            with open(BLOCKS_DIR / f"{block_name}.csv", newline="") as block_file:
                next(block_file)                      # skip this block's header
                for line in block_file:
                    rows_read += 1
                    household_id, rest = line.split(",", 1)
                    is_chosen = household_id in chosen_ids
                    is_sample_year = rest.startswith(SAMPLE_YEAR)
                    if is_chosen and is_sample_year:
                        out.write(line)
                        rows_kept += 1

    return rows_read, rows_kept


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--first-block", type=int, default=0)
    parser.add_argument("--last-block", type=int, default=TOTAL_BLOCKS - 1)
    parser.add_argument("--out", type=Path,
                        default=INTERIM_DIR / "london_halfhourly_sample.csv")
    parser.add_argument("--no-header", action="store_true")
    args = parser.parse_args()

    INTERIM_DIR.mkdir(parents=True, exist_ok=True)

    all_households = build_sampling_frame()
    coverage_path = write_csv(all_households, INTERIM_DIR / "household_coverage_2013.csv")
    eligibility_counts = Counter(h["eligible"] for h in all_households)
    print(f"Sampling frame ({len(all_households)} households) -> {coverage_path}")
    for reason, count in sorted(eligibility_counts.items()):
        print(f"    {reason}: {count}")

    chosen = choose_households(all_households)
    household_list_path = write_csv(chosen, INTERIM_DIR / "sampled_households.csv")
    print(f"Chose {len(chosen)} households -> {household_list_path}")

    rows_read, rows_kept = copy_readings(
        chosen, args.first_block, args.last_block, args.out,
        write_header=not args.no_header,
    )
    print(f"Blocks {args.first_block}-{args.last_block}: "
          f"read {rows_read:,} rows, kept {rows_kept:,} -> {args.out}")


if __name__ == "__main__":
    main()
