#!/bin/bash
# Start the SQL Server engine, then the app, in one container.
#
# They are together rather than in separate services because the engine is
# only ever addressed over loopback by this one app, holds nothing that
# outlives a request, and attaches database files from a directory the app
# writes. Splitting them would mean shipping those files between containers to
# no benefit.
set -euo pipefail

: "${PORT:=8080}"

start_sqlserver() {
  if [ -z "${MSSQL_SA_PASSWORD:-}" ]; then
    echo "entrypoint: MSSQL_SA_PASSWORD unset - starting without SQL Server."
    echo "entrypoint: study results will work; project models will not."
    return 0
  fi

  /opt/mssql/bin/sqlservr &

  # The engine reaches ready in about four seconds. Waiting for it keeps the
  # first model upload after a cold start from arriving before the server
  # does; the app still serves study results either way, so a failure here is
  # logged rather than fatal.
  for _ in $(seq 1 60); do
    if /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P "$MSSQL_SA_PASSWORD" \
         -C -N -l 2 -Q "SELECT 1" >/dev/null 2>&1; then
      echo "entrypoint: SQL Server ready."
      return 0
    fi
    sleep 1
  done
  echo "entrypoint: SQL Server did not become ready; continuing without it."
}

start_sqlserver

# One worker on purpose. Load jobs are tracked in an in-memory dict, so a
# second worker would answer status polls for jobs it has never heard of.
# Concurrency comes from threads instead.
#
# --timeout 0 because deriving a year of hourly results takes ~25s, well past
# gunicorn's 30s default once the file is large; the platform enforces its own
# request deadline.
exec gunicorn --bind ":$PORT" --workers 1 --threads 8 --timeout 0 app:app
