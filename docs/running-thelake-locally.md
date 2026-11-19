# Running thelake locally for this project

These are the steps used for the results in the README: thelake built from source at commit `c90afd7` (2026-11-18), on Linux x86_64, with local Postgres and the local filesystem as the DuckLake data path. On a normal developer machine with Docker, thelake's own `make setup && make run` is simpler. Use that and skip the "offline" notes.

## 1. Catalog database

```bash
# any Postgres 14+ works; thelake's docker-compose also provides one
createuser -s ducklake && psql -c "ALTER USER ducklake PASSWORD 'ducklake'" && createdb -O ducklake ducklake
```

## 2. Build thelake

```bash
git clone https://github.com/softprobe/thelake && cd thelake
bash scripts/download-ducklake-extension.sh          # pinned DuckLake build -> target/ducklake-extension/
(cd packages/thelake-explorer && npm ci && npm run build:embedded)   # Explorer UI
DUCKDB_DOWNLOAD_LIB=1 cargo build --bin thelake
```

## 3. Config

Copy `config.yaml` and point it at local paths:

```yaml
ducklake:
  metadata_path: "host=localhost port=5432 dbname=ducklake user=ducklake password=ducklake"
  data_path: "/abs/path/warehouse/data/"
  extension_path: "/abs/path/thelake/target/ducklake-extension/ducklake.duckdb_extension"
```

## 4. Run

```bash
export CONFIG_FILE=/abs/path/thelake-local.yaml
export SOFTPROBE_ADMIN_API_KEY=dev-admin
export SOFTPROBE_ASSERTION_HMAC_SECRET=dev-assertion
export SOFTPROBE_GRPC_DISABLE=1          # this project exports OTLP over HTTP
./target/debug/thelake
curl localhost:8090/ready                # {"status":"ready",...}
```

`thelake_auth.py` defaults to the same admin key and HMAC secret. Override them with the same environment variables.

## Offline notes (sandbox without extensions.duckdb.org)

The sandbox used here could reach GitHub, PyPI and npm, but not the DuckDB extension repository. What worked:

| Need | Workaround |
|---|---|
| `libduckdb.so` 2.0.0 for the Rust build | `npm pack @duckdb/node-bindings-linux-x64@2.0.0-r.1`, then `DUCKDB_LIB_DIR=<package dir>` and `LD_LIBRARY_PATH` at runtime |
| `httpfs`, `postgres_scanner`, `iceberg`, `avro` extensions | PyPI wheels `duckdb-extension-<name>==2.0.0`; copy the `.duckdb_extension` files into `~/.duckdb/extensions/v2.0.0/linux_amd64/` (`SET unsafe_enable_version_guessing` autoloads `iceberg`, which needs `avro`) |
| `cache_httpfs` (community extension, query lane) | `PERF_DISABLE_CACHE_HTTPFS=1` |
