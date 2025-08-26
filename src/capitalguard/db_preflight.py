# src/capitalguard/db_preflight.py
import os
import sqlalchemy as sa

def _normalize(url: str) -> str:
    url = (url or "").strip()
    return url.replace("postgres://", "postgresql+psycopg://", 1) if url.startswith("postgres://") else url

def main():
    url = _normalize(os.getenv("DATABASE_URL", ""))
    if not url:
        print("⚠️ DATABASE_URL is empty; skip preflight")
        return

    engine = sa.create_engine(url, future=True)
    with engine.begin() as conn:
        # 1) تأكيد وجود جدول alembic_version وتوسيع العمود
        conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS alembic_version (
            version_num VARCHAR(255) NOT NULL PRIMARY KEY
        )
        """)
        conn.exec_driver_sql("""
        ALTER TABLE alembic_version
        ALTER COLUMN version_num TYPE VARCHAR(255)
        """)

        # 2) ضمان الأعمدة الجديدة إن لم تكن موجودة
        conn.exec_driver_sql("""
        ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS exit_price DOUBLE PRECISION
        """)
        conn.exec_driver_sql("""
        ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS closed_at TIMESTAMP
        """)

    print("✅ Preflight done: alembic_version OK, exit_price/closed_at ensured.")

if __name__ == "__main__":
    main()