// queries.js - MongoDB queries for the Rooftop Solar and Household Equity project.
//
// Database: solar_equity
//   solar_systems  one document per system (2 modelled PVGIS systems, 44 inverters)
//   solar_daily    one document per system per day, readings embedded as an array
//
// Run from the project root (PowerShell), after mongodb/02_import_data.py:
//   docker compose cp mongodb/queries.js mongo:/tmp/queries.js
//   docker compose exec -T mongo sh -c 'mongosh --quiet -u $MONGO_INITDB_ROOT_USERNAME -p $MONGO_INITDB_ROOT_PASSWORD --authenticationDatabase admin /tmp/queries.js' | Out-File -Encoding utf8 mongodb/query_results.txt
//
// The script can be re-run: it drops its own index at the start.

db = db.getSiblingDB("solar_equity");

function show(title, documents) {
    print("\n=== " + title + " ===");
    documents.forEach(function (document) { print(EJSON.stringify(document)); });
    print("(" + documents.length + " documents)");
}

// Start without the project index so Q8 can show its effect.
try { db.solar_daily.dropIndex("system_day"); } catch (error) { /* not created yet */ }


// ---------------------------------------------------------------------------------------
// Q1. Filter with comparison and logical operators ($gte, $gt, $or, implicit AND).
// Shows: the worst inverter-days - days with at least 20 fault readings OR more than
// 24 missing log lines (6 hours) - on days when the sun was actually up.
// ---------------------------------------------------------------------------------------
show("Q1. Worst inverter-days (faults or missing data)",
    db.solar_daily.find(
        {
            kind: "inverter",
            "summary.sunlit_readings": { $gt: 0 },
            $or: [
                { "summary.fault_readings": { $gte: 20 } },
                { "summary.missing_readings": { $gt: 24 } }
            ]
        },
        { _id: 0, system_id: 1, date: 1, "summary.fault_readings": 1,
          "summary.missing_readings": 1, "summary.energy_kwh": 1 }
    ).sort({ "summary.fault_readings": -1, "summary.missing_readings": -1 }).limit(8).toArray());


// ---------------------------------------------------------------------------------------
// Q2. Projection and sorting.
// Shows: the five sunniest days of 2013 in Beirut, returning only the date, the energy
// produced per kW of panel, and the number of hours with output.
// ---------------------------------------------------------------------------------------
show("Q2. Five sunniest days in Beirut",
    db.solar_daily.find(
        { system_id: "pvgis_beirut" },
        { _id: 0, date: 1, "summary.energy_kwh_per_kwp": 1, "summary.sun_hours": 1 }
    ).sort({ "summary.energy_kwh_per_kwp": -1 }).limit(5).toArray());


// ---------------------------------------------------------------------------------------
// Q3. Aggregation pipeline: $match, $group, $sort.
// Shows: solar energy per kW of panel by season in London and in Beirut. This is the
// supply side of sub-question 2 (how much demand solar can cover, by season).
// ---------------------------------------------------------------------------------------
show("Q3. Seasonal solar yield per city (kWh per kWp)",
    db.solar_daily.aggregate([
        { $match: { kind: "modelled" } },
        { $group: {
            _id: { city: "$city", season: "$season" },
            total_kwh_per_kwp: { $sum: "$summary.energy_kwh_per_kwp" },
            avg_kwh_per_day: { $avg: "$summary.energy_kwh_per_kwp" },
            days: { $sum: 1 }
        } },
        { $sort: { "_id.city": 1, total_kwh_per_kwp: -1 } }
    ]).toArray());


// ---------------------------------------------------------------------------------------
// Q4. $unwind on the embedded readings array.
// Shows: the average summer day in each city, hour by hour (UTC) - the solar "curve"
// that is compared with the household demand curve from PostgreSQL.
// ---------------------------------------------------------------------------------------
show("Q4. Average summer solar output by hour (kWh per kWp)",
    db.solar_daily.aggregate([
        { $match: { kind: "modelled", season: "Summer" } },
        { $unwind: "$readings" },
        { $group: {
            _id: { city: "$city", hour: "$readings.hour" },
            avg_kwh_per_kwp: { $avg: "$readings.pv_kwh_per_kwp" }
        } },
        { $match: { avg_kwh_per_kwp: { $gt: 0.05 } } },
        { $sort: { "_id.city": 1, "_id.hour": 1 } }
    ]).toArray());


// ---------------------------------------------------------------------------------------
// Q5. $unwind + $match on array elements + $group.
// Shows: for each plant, how many sunlit readings were normal and how many were faults
// (zero output in sunlight), with the average power in each case. This is the
// evidence for sub-question 4: real equipment falls short of modelled output.
// ---------------------------------------------------------------------------------------
show("Q5. Normal versus fault readings in sunlight, per plant",
    db.solar_daily.aggregate([
        { $match: { kind: "inverter" } },
        { $unwind: "$readings" },
        { $match: { "readings.irradiation": { $gt: 0.1 } } },
        { $group: {
            _id: { plant: "$plant", is_fault: "$readings.is_fault" },
            readings: { $sum: 1 },
            avg_ac_power_kw: { $avg: "$readings.ac_power_kw" },
            avg_irradiation: { $avg: "$readings.irradiation" }
        } },
        { $sort: { "_id.plant": 1, "_id.is_fault": 1 } }
    ]).toArray());


// ---------------------------------------------------------------------------------------
// Q6. $lookup: join solar_daily to solar_systems (the referenced collection).
// Shows: each modelled system's yearly output next to its location and panel setup,
// which live in solar_systems and are stored once, not repeated on every day.
// ---------------------------------------------------------------------------------------
show("Q6. Yearly output with system details ($lookup)",
    db.solar_daily.aggregate([
        { $match: { kind: "modelled" } },
        { $group: {
            _id: "$system_id",
            year_kwh_per_kwp: { $sum: "$summary.energy_kwh_per_kwp" },
            days: { $sum: 1 }
        } },
        { $lookup: {
            from: "solar_systems",
            localField: "_id",
            foreignField: "_id",
            as: "system"
        } },
        { $unwind: "$system" },
        { $project: {
            _id: 0,
            system_id: "$_id",
            city: "$system.city",
            coordinates: "$system.location.coordinates",
            slope_deg: "$system.setup.slope_deg",
            system_loss_pct: "$system.setup.system_loss_pct",
            year_kwh_per_kwp: 1,
            days: 1
        } },
        { $sort: { year_kwh_per_kwp: -1 } }
    ]).toArray());


// ---------------------------------------------------------------------------------------
// Q7. Array query with $elemMatch.
// Shows: how many inverter-days in each plant contain at least one reading where the
// inverter produced nothing in strong sunlight (irradiation of 0.8 or more).
// $elemMatch makes both conditions apply to the SAME reading in the array.
// ---------------------------------------------------------------------------------------
["plant_1", "plant_2"].forEach(function (plant) {
    var days_with_outage = db.solar_daily.countDocuments({
        plant: plant,
        readings: { $elemMatch: { is_fault: true, irradiation: { $gte: 0.8 } } }
    });
    var all_days = db.solar_daily.countDocuments({ plant: plant });
    print("\n=== Q7. " + plant + ": inverter-days with an outage in strong sun ===");
    print(days_with_outage + " of " + all_days + " inverter-days");
});


// ---------------------------------------------------------------------------------------
// Q8. Index and explain().
// Shows: the same lookup (one system on one day) before and after creating an index
// on (system_id, date). Without the index MongoDB scans every document (COLLSCAN);
// with it, MongoDB jumps straight to the one it needs (IXSCAN).
// ---------------------------------------------------------------------------------------
function explainLookup(label) {
    var plan = db.solar_daily.find(
        { system_id: "pvgis_beirut", date: ISODate("2013-07-15T00:00:00Z") }
    ).explain("executionStats");
    var stats = plan.executionStats || {};
    var winning = (plan.queryPlanner || {}).winningPlan || {};
    var stage = winning.inputStage ? winning.inputStage.stage : winning.stage;
    print(label + ": stage=" + stage +
          ", documents examined=" + stats.totalDocsExamined +
          ", keys examined=" + stats.totalKeysExamined +
          ", returned=" + stats.nReturned);
}

print("\n=== Q8. Effect of the index on (system_id, date) ===");
explainLookup("Before index");
db.solar_daily.createIndex({ system_id: 1, date: 1 }, { name: "system_day", unique: true });
explainLookup("After index ");
print("Indexes on solar_daily: " + db.solar_daily.getIndexes().map(function (index) { return index.name; }).join(", "));
