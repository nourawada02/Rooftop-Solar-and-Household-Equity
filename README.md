# Rooftop Solar and Household Equity

**Guiding question:** Would rooftop solar cut grid demand most for the households that need it most?

"Need it most" means households in the lowest-income socio-economic category of the
smart-meter dataset (the ACORN "Adversity" group), compared with the "Comfortable"
and "Affluent" groups.

Intro to Data Science final project, Theme 2: Household energy and solar.

## Sub-questions

1. How do consumption level and timing (daytime versus evening peak) differ across socio-economic groups?
2. How much of each group's demand could rooftop solar cover, by season?
3. How much does a home battery add to solar self-consumption and estimated savings?
4. How far does real inverter output fall short of modelled output, and how does that change the estimates?

## Repository layout

| Folder | Contents |
| --- | --- |
| `data/raw/` | Untouched downloads (not committed; see below) |
| `data/clean/` | Cleaned CSV/JSON files produced by the cleaning scripts |
| `cleaning/` | Profiling and cleaning notebooks or scripts, plus `cleaning_log.md` |
| `sql/` | `schema.sql`, `load.sql`, `queries.sql` |
| `mongodb/` | `schema.md`, `import.md`, `queries.js` |
| `powerbi/` | Final `.pbix` file and PDF export of each page |
| `scripts/` | Helper scripts (PVGIS download) |

## How to reproduce

### 1. Get the data

1. Download [Smart Meters in London](https://www.kaggle.com/datasets/jeanmidev/smart-meters-in-london)
   from Kaggle and unzip it into `data/raw/london/`.
2. Download [Solar Power Generation Data](https://www.kaggle.com/datasets/anikannal/solar-power-generation-data)
   from Kaggle and unzip it into `data/raw/inverters/`.
3. Fetch modelled solar output for London and Beirut from the PVGIS API:

   ```
   pip install -r requirements.txt
   python scripts/fetch_pvgis.py
   ```

   This writes one JSON file per city into `data/raw/pvgis/`.

### 2. Start the databases

```
cp .env.example .env      # then change the passwords
docker compose up -d
```

PostgreSQL listens on port 5432 and MongoDB on port 27017.

### 3. Clean, load, query

Steps to be added as each phase is completed:

- [ ] Run the cleaning scripts in `cleaning/`
- [ ] Load PostgreSQL with `sql/load.sql`
- [ ] Import MongoDB with the commands in `mongodb/import.md`
- [ ] Open `powerbi/` dashboard

## Data sources

| Source | Provider | Store |
| --- | --- | --- |
| Smart Meters in London | UK Power Networks, via Kaggle | PostgreSQL |
| PVGIS hourly radiation API | EU Joint Research Centre | MongoDB |
| Solar Power Generation Data | Kaggle | MongoDB |

## AI use

The course policy requires stating where and how AI tools were used.

- Claude (Anthropic) helped brainstorm the topic, draft the proposal, and create this
  repository skeleton (folder layout, Docker Compose file, PVGIS download script, templates).
- Add further uses here as the project progresses.

## Team

See `contributions.md`.
