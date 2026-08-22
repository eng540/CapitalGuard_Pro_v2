"""Verify the Core Alembic chain on an empty PostgreSQL database.

This script intentionally checks schema presence and empty-domain counts only.
It never connects to Railway or processes production data.
"""

from __future__ import annotations

import os

import psycopg


DATABASE_URL = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
REQUIRED_TABLES = ("users", "recommendations", "user_trades", "alembic_version")


def main() -> None:
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            for table in REQUIRED_TABLES:
                cursor.execute("SELECT to_regclass(%s)", (f"public.{table}",))
                if cursor.fetchone()[0] != table:
                    raise SystemExit(f"required table missing after fresh migration: {table}")
            cursor.execute("SELECT version_num FROM alembic_version")
            revision = cursor.fetchone()
            if not revision or not revision[0]:
                raise SystemExit("alembic_version is empty after fresh migration")
            for table in ("users", "recommendations", "user_trades"):
                cursor.execute(f"SELECT count(*) FROM {table}")
                if cursor.fetchone()[0] != 0:
                    raise SystemExit(f"fresh migration database unexpectedly contains rows: {table}")
    print("Fresh PostgreSQL migration verified: schema=head domain_rows=0")


if __name__ == "__main__":
    main()
