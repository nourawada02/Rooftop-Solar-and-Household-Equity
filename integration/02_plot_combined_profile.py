"""Step 4 (chart) - Household demand against rooftop solar on an average day.

Reads integration/combined_profile.csv (written by 01_integrate_and_simulate.py), which
already joins the PostgreSQL demand profile to the MongoDB solar profile on season and
clock hour, and draws one panel per season.

Run from the project root:
    python integration/02_plot_combined_profile.py

Output:
    integration/figures/demand_vs_solar.png
"""

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INPUT_PATH = PROJECT_ROOT / "integration" / "combined_profile.csv"
OUTPUT_PATH = PROJECT_ROOT / "integration" / "figures" / "demand_vs_solar.png"

PANEL_KWP = 3.0
SEASONS = ["Winter", "Summer"]
CATEGORY_COLORS = {"Adversity": "#2a78d6", "Comfortable": "#1baf7a", "Affluent": "#e87ba4"}
SOLAR_COLOR, TEXT, MUTED, SURFACE, GRID = "#eda100", "#0b0b0b", "#52514e", "#fcfcfb", "#e4e3df"

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": GRID, "axes.labelcolor": MUTED, "text.color": TEXT,
    "xtick.color": MUTED, "ytick.color": MUTED, "axes.grid": True, "grid.color": GRID,
    "axes.spines.top": False, "axes.spines.right": False, "font.size": 10,
    "axes.titlesize": 11, "axes.titleweight": "bold", "axes.titlelocation": "left",
})


def main(input_path=INPUT_PATH, output_path=OUTPUT_PATH):
    profile = pd.read_csv(input_path)
    figure, axes = plt.subplots(1, len(SEASONS), figsize=(11, 4.2), sharey=True)

    for axis, season in zip(axes, SEASONS):
        in_season = profile[profile["season"] == season]
        solar = (in_season.drop_duplicates("clock_hour").sort_values("clock_hour"))
        solar_kwh = solar["london_solar_kwh_per_kwp"] * PANEL_KWP
        axis.fill_between(solar["clock_hour"], solar_kwh, color=SOLAR_COLOR, alpha=0.25, linewidth=0)
        axis.plot(solar["clock_hour"], solar_kwh, color=SOLAR_COLOR, linewidth=2,
                  label=f"Solar output, {PANEL_KWP:g} kWp (London)")

        for category, color in CATEGORY_COLORS.items():
            rows = in_season[in_season["category_name"] == category].sort_values("clock_hour")
            axis.plot(rows["clock_hour"], rows["demand_kwh"], color=color, linewidth=2,
                      label=f"Demand: {category}")

        axis.set_title(season)
        axis.set_xlabel("Hour of the day (London clock time)")
        axis.set_xticks(range(0, 24, 3))
        axis.set_xlim(0, 23)

    axes[0].set_ylabel("kWh per hour, per household")
    axes[0].set_ylim(bottom=0)
    axes[-1].legend(frameon=False, loc="upper left", fontsize=9)
    figure.suptitle("Average day: household demand against rooftop solar",
                    x=0.01, ha="left", fontsize=12, fontweight="bold")
    figure.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, dpi=150)
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
