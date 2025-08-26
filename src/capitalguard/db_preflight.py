# src/capitalguard/db_preflight.py
import os
import sqlalchemy as sa

def _normalize_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    return url

def main():
    url = _normalize_url(os.getenv("DATABASE_URL", ""))
    if not url:
        print("⚠️ DATABASE_URL is empty; skip preflight")
        return

    engine = sa.create_engine(url, future=True)
    with engine.begin() as conn:
        # 1) ensure alembic_version table exists
        conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS alembic_version (
            version_num VARCHAR(255) NOT NULL PRIMARY KEY
        )
        """)
        # 2) widen version_num length if needed
        # (ALTER TYPE is idempotent towards widening on PG)
        conn.exec_driver_sql("""
        ALTER TABLE alembic_version
        ALTER COLUMN version_num TYPE VARCHAR(255)
        """)
        # 3) safety net: add our new columns if missing
        conn.exec_driver_sql("""
        ALTER TABLE recommendations
        ADD COLUMN IF NOT EXISTS exit_price DOUBLE PRECISION
        """)
        conn.exec_driver_sql("""
        ALTER TABLE recommendations
        ADD COLUMN IF NOT EXISTS closed_at TIMESTAMP
        """)

    print("✅ Preflight done: alembic_version OK, exit_price/closed_at ensured.")

if __name__ == "__main__":
    main()