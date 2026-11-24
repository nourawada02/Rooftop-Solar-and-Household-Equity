"""Step 3.2 - Import the documents into MongoDB with pymongo.

Reads the username and password from the project's .env file (the same ones Docker
used to create the database), so no password is typed or stored in this script.

Run from the project root (Docker must be running):
    python mongodb/02_import_data.py

Result: database `solar_equity` with collections `solar_systems` and `solar_daily`.
The script can be re-run: it replaces both collections.
"""

import argparse
from pathlib import Path

from bson import json_util
from pymongo import MongoClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CLEAN = PROJECT_ROOT / "data" / "clean"
DATABASE_NAME = "solar_equity"
BATCH_SIZE = 200


def read_env_file():
    """Return the KEY=value pairs from .env as a dictionary."""
    settings = {}
    for line in (PROJECT_ROOT / ".env").read_text().splitlines():
        is_setting = "=" in line and not line.strip().startswith("#")
        if is_setting:
            key, value = line.split("=", 1)
            settings[key.strip()] = value.strip()
    return settings


def load_collection(database, name, path):
    collection = database[name]
    collection.drop()
    batch = []
    total = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            batch.append(json_util.loads(line))     # turns {"$date": ...} into real dates
            if len(batch) == BATCH_SIZE:
                collection.insert_many(batch)
                total += len(batch)
                batch = []
    if batch:
        collection.insert_many(batch)
        total += len(batch)
    print(f"{name}: inserted {total:,} documents (collection now has "
          f"{collection.count_documents({}):,})")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=27017)
    args = parser.parse_args()

    settings = read_env_file()
    client = MongoClient(
        host="localhost", port=args.port,
        username=settings["MONGO_INITDB_ROOT_USERNAME"],
        password=settings["MONGO_INITDB_ROOT_PASSWORD"],
        authSource="admin",
    )

    database = client[DATABASE_NAME]
    load_collection(database, "solar_systems", CLEAN / "solar_systems.jsonl")
    load_collection(database, "solar_daily", CLEAN / "solar_daily.jsonl")

    example = database["solar_daily"].find_one({"kind": "inverter"}, {"readings": {"$slice": 1}})
    print("Example document (first reading only):")
    print(json_util.dumps(example, indent=2))


if __name__ == "__main__":
    main()
