#!/usr/bin/env bash
# Intentionally absent: there is no data-push path.
# Postgres lives on volume topo_ng_pg_data. Code updates never mount or replace it.
# If a data change is required, the human must say so explicitly.
echo "FATAL: no data-push script. Will not overwrite the database volume." >&2
exit 1
