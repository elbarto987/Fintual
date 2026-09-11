#!/bin/sh
set -e

echo "Waiting for Postgres at ${POSTGRES_HOST:-localhost}:${POSTGRES_PORT:-5432}..."
python - <<'PYEOF'
import os
import socket
import sys
import time

host = os.environ.get("POSTGRES_HOST", "localhost")
port = int(os.environ.get("POSTGRES_PORT", "5432"))

for attempt in range(30):
    try:
        with socket.create_connection((host, port), timeout=2):
            sys.exit(0)
    except OSError:
        time.sleep(1)
else:
    print(f"Postgres never became reachable at {host}:{port}", file=sys.stderr)
    sys.exit(1)
PYEOF

echo "Postgres is up. Applying migrations..."
python manage.py migrate --noinput

echo "Starting: $@"
exec "$@"
