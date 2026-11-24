# MongoDB import

Run these from the project root, with Docker running (`docker compose up -d`).
All commands run inside the MongoDB container and take the username and password
from the container itself, so nothing depends on what else is installed on the computer.

## 1. Build the documents

```
python mongodb/01_build_documents.py
```

Reads the cleaned CSV files and writes `data/clean/solar_systems.jsonl` and
`data/clean/solar_daily.jsonl` (MongoDB Extended JSON, one document per line).

## 2. Import with mongoimport

The data folder is mounted in the container at `/data/clean`. `--drop` replaces the
collection, so the commands can be re-run.

```
docker compose exec mongo sh -c 'mongoimport -u $MONGO_INITDB_ROOT_USERNAME -p $MONGO_INITDB_ROOT_PASSWORD --authenticationDatabase admin --db solar_equity --collection solar_systems --drop --file /data/clean/solar_systems.jsonl'
docker compose exec mongo sh -c 'mongoimport -u $MONGO_INITDB_ROOT_USERNAME -p $MONGO_INITDB_ROOT_PASSWORD --authenticationDatabase admin --db solar_equity --collection solar_daily --drop --file /data/clean/solar_daily.jsonl'
```

Expected: 46 documents in `solar_systems` and 2,194 in `solar_daily`.

## 3. Run the queries

```
docker compose cp mongodb/queries.js mongo:/tmp/queries.js
docker compose exec -T mongo sh -c 'mongosh --quiet -u $MONGO_INITDB_ROOT_USERNAME -p $MONGO_INITDB_ROOT_PASSWORD --authenticationDatabase admin /tmp/queries.js' | Out-File -Encoding utf8 mongodb/query_results.txt
```

## Alternative: import with pymongo

```
pip install pymongo
python mongodb/02_import_data.py
```

This connects from Windows to `localhost:27017` using the login in `.env`. If another
MongoDB is installed on the computer and already uses port 27017, the connection
reaches that one instead and fails with "Authentication failed". In that case use
the mongoimport commands above, or map the container to another port in
`docker-compose.yml` (for example `"27018:27017"`) and run the script with `--port 27018`.
