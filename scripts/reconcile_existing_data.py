"""Read-only, masked reconciliation report for an isolated Core PostgreSQL copy."""

from __future__ import annotations

import json
import os

import psycopg


REQUIRED_TABLES = ("users", "recommendations", "user_trades", "publication_deliveries", "web_command_audit", "alembic_version")
STATUS_TABLES = ("recommendations", "user_trades", "publication_deliveries")


def _url() -> str:
    value = os.environ.get("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required for reconciliation")
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def _table_exists(cursor: psycopg.Cursor, table: str) -> bool:
    cursor.execute("SELECT to_regclass(%s)", (f"public.{table}",))
    return cursor.fetchone()[0] == table


def _status_counts(cursor: psycopg.Cursor, table: str) -> dict[str, int]:
    cursor.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = %s AND column_name = 'status'",
        (table,),
    )
    if cursor.fetchone() is None:
        return {}
    cursor.execute(f"SELECT COALESCE(status::text, 'NULL'), count(*) FROM {table} GROUP BY 1 ORDER BY 1")
    return {str(status): int(count) for status, count in cursor.fetchall()}


def main() -> None:
    expected_db = os.environ.get("RECONCILIATION_EXPECTED_DB", "").strip()
    if not expected_db:
        raise SystemExit("RECONCILIATION_EXPECTED_DB is required to guard the target database")

    with psycopg.connect(_url(), autocommit=False) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute("SELECT current_database()")
            database_name = cursor.fetchone()[0]
            if database_name != expected_db:
                raise SystemExit("connected database does not match RECONCILIATION_EXPECTED_DB")

            missing = [table for table in REQUIRED_TABLES if not _table_exists(cursor, table)]
            if missing:
                raise SystemExit(f"required reconciliation tables missing: {', '.join(missing)}")

            cursor.execute("SELECT version_num FROM alembic_version")
            row = cursor.fetchone()
            if not row or not row[0]:
                raise SystemExit("alembic_version is empty")

            table_counts: dict[str, int] = {}
            for table in REQUIRED_TABLES[:-1]:
                cursor.execute(f"SELECT count(*) FROM {table}")
                table_counts[table] = int(cursor.fetchone()[0])

            cursor.execute("SELECT count(*) FROM pg_constraint WHERE contype = 'f' AND NOT convalidated")
            unvalidated_foreign_keys = int(cursor.fetchone()[0])
            report = {
                "schema_revision": row[0],
                "database": database_name,
                "table_counts": table_counts,
                "status_counts": {table: _status_counts(cursor, table) for table in STATUS_TABLES},
                "unvalidated_foreign_key_count": unvalidated_foreign_keys,
                "read_only": True,
                "masked": True,
            }
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
