"""Container health check for both the HTTP process and PostgreSQL."""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

import psycopg2


def main() -> int:
    try:
        password_file = Path(os.getenv("DATABASE_PASSWORD_FILE", "/run/secrets/db_password"))
        password = password_file.read_text(encoding="utf-8").strip()
        connection = psycopg2.connect(
            host=os.getenv("DATABASE_HOST", "db"),
            port=int(os.getenv("DATABASE_PORT", "5432")),
            user=os.getenv("POSTGRES_USER", "ecoaegis"),
            password=password,
            dbname=os.getenv("POSTGRES_DB", "ecoaegis"),
            connect_timeout=3,
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                if cursor.fetchone() != (1,):
                    return 1
        finally:
            connection.close()

        with urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=3) as response:
            if response.status != 200:
                return 1
            payload = json.loads(response.read().decode("utf-8"))
            if payload.get("status") != "ok" or payload.get("app") != "ecoaegis":
                return 1
        return 0
    except Exception as exc:  # no secret-bearing exception text in container logs
        print(f"healthcheck failed: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
