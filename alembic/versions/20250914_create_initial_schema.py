"""Safe initial schema - idempotent

Revision ID: 20250914_create_initial_schema
Revises:
Create Date: 2025-09-14 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20250914_create_initial_schema'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # =========================
    # ENUM Types (idempotent)
    # =========================
    op.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'recommendationstatus') THEN
            CREATE TYPE recommendationstatus AS ENUM ('PENDING', 'ACTIVE', 'CLOSED');
        END IF;
    END$$;
    """)
    op.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'ordertype') THEN
            CREATE TYPE ordertype AS ENUM ('MARKET', 'LIMIT', 'STOP_MARKET');
        END IF;
    END$$;
    """)
    op.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'exitstrategy') THEN
            CREATE TYPE exitstrategy AS ENUM ('CLOSE_AT_FINAL_TP', 'MANUAL_CLOSE_ONLY');
        END IF;
    END$$;
    """)

    # =========================
    # Tables (idempotent)
    # =========================
    op.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id                SERIAL PRIMARY KEY,
        email             VARCHAR NOT NULL UNIQUE,
        hashed_password   VARCHAR,
        is_active         BOOLEAN NOT NULL DEFAULT true,
        telegram_user_id  BIGINT  NOT NULL UNIQUE,
        user_type         VARCHAR(50) NOT NULL DEFAULT 'trader',
        created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
        first_name        VARCHAR
    );
    """)

    op.execute("""
    CREATE TABLE IF NOT EXISTS roles (
        id   SERIAL PRIMARY KEY,
        name VARCHAR(64) UNIQUE NOT NULL
    );
    """)

    op.execute("""
    CREATE TABLE IF NOT EXISTS user_roles (
        id       SERIAL PRIMARY KEY,
        user_id  INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        role_id  INT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
        UNIQUE(user_id, role_id)
    );
    """)

    op.execute("""
    CREATE TABLE IF NOT EXISTS channels (
        id                   SERIAL PRIMARY KEY,
        user_id              INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        telegram_channel_id  BIGINT NOT NULL UNIQUE,
        username             VARCHAR(255),
        title                VARCHAR(255),
        is_active            BOOLEAN NOT NULL DEFAULT true,
        created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_verified_at     TIMESTAMPTZ,
        notes                TEXT
    );
    """)

    op.execute("""
    CREATE TABLE IF NOT EXISTS recommendations (
        id                      SERIAL PRIMARY KEY,
        user_id                 INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        asset                   VARCHAR NOT NULL,
        side                    VARCHAR NOT NULL,
        entry                   FLOAT   NOT NULL,
        stop_loss               FLOAT   NOT NULL,
        targets                 JSON    NOT NULL,
        order_type              ordertype NOT NULL DEFAULT 'LIMIT',
        status                  recommendationstatus NOT NULL DEFAULT 'PENDING',
        channel_id              BIGINT,
        message_id              BIGINT,
        published_at            TIMESTAMPTZ,
        market                  VARCHAR,
        notes                   TEXT,
        exit_price              FLOAT,
        activated_at            TIMESTAMPTZ,
        closed_at               TIMESTAMPTZ,
        created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
        alert_meta              JSONB NOT NULL DEFAULT '{}'::jsonb,
        highest_price_reached   FLOAT,
        lowest_price_reached    FLOAT,
        exit_strategy           exitstrategy NOT NULL DEFAULT 'CLOSE_AT_FINAL_TP',
        profit_stop_price       FLOAT,
        open_size_percent       FLOAT NOT NULL DEFAULT 100.0
    );
    """)

    op.execute("""
    CREATE TABLE IF NOT EXISTS published_messages (
        id                   SERIAL PRIMARY KEY,
        recommendation_id    INT NOT NULL REFERENCES recommendations(id) ON DELETE CASCADE,
        telegram_channel_id  BIGINT NOT NULL,
        telegram_message_id  BIGINT NOT NULL,
        published_at         TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    """)

    op.execute("""
    CREATE TABLE IF NOT EXISTS recommendation_events (
        id                 SERIAL PRIMARY KEY,
        recommendation_id  INT NOT NULL REFERENCES recommendations(id) ON DELETE CASCADE,
        event_type         VARCHAR(50) NOT NULL,
        event_timestamp    TIMESTAMPTZ NOT NULL DEFAULT now(),
        event_data         JSONB
    );
    """)

    # =========================
    # Indexes (idempotent)
    # =========================
    # users: email unique & telegram_user_id unique موجودة كقيود، لكن نضيف فهارس إضافية اختيارية للبحث.
    op.execute("CREATE INDEX IF NOT EXISTS ix_users_email ON users (email);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_users_telegram_user_id ON users (telegram_user_id);")

    # channels: فهرس على user_id + فهرس فريد غير حساس لحالة الأحرف لاسم المستخدم (NULL-safe)
    op.execute("CREATE INDEX IF NOT EXISTS ix_channels_user_id ON channels (user_id);")
    op.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS uq_channels_username_ci
    ON channels (lower(username))
    WHERE username IS NOT NULL;
    """)

    # recommendations: فهارس للاستعلام السريع
    op.execute("CREATE INDEX IF NOT EXISTS ix_recommendations_asset ON recommendations (asset);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_recommendations_channel_id ON recommendations (channel_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_recommendations_status ON recommendations (status);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_recommendations_user_id ON recommendations (user_id);")
    # فهرس على published_at لتقارير زمنية
    op.execute("CREATE INDEX IF NOT EXISTS ix_recommendations_published_at ON recommendations (published_at);")

    # published_messages
    op.execute("""
    CREATE INDEX IF NOT EXISTS ix_published_messages_recommendation_id
    ON published_messages (recommendation_id);
    """)

    # recommendation_events
    op.execute("""
    CREATE INDEX IF NOT EXISTS ix_recommendation_events_event_type
    ON recommendation_events (event_type);
    """)
    op.execute("""
    CREATE INDEX IF NOT EXISTS ix_recommendation_events_recommendation_id
    ON recommendation_events (recommendation_id);
    """)

    # =========================
    # Trigger function & trigger (idempotent)
    # =========================
    op.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'set_updated_at') THEN
            CREATE FUNCTION set_updated_at()
            RETURNS TRIGGER AS $func$
            BEGIN
                NEW.updated_at = now();
                RETURN NEW;
            END;
            $func$ LANGUAGE plpgsql;
        END IF;
    END$$;
    """)

    op.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_recommendations_set_updated_at') THEN
            CREATE TRIGGER trg_recommendations_set_updated_at
            BEFORE UPDATE ON recommendations
            FOR EACH ROW
            EXECUTE FUNCTION set_updated_at();
        END IF;
    END$$;
    """)


def downgrade() -> None:
    # إسقاط التريغر والدالة
    op.execute("DROP TRIGGER IF EXISTS trg_recommendations_set_updated_at ON recommendations;")
    op.execute("DROP FUNCTION IF EXISTS set_updated_at;")

    # إسقاط الفهارس (اختياري عند الـ CASCADE على الجداول، لكن نضعها لسهولة الرجوع)
    op.execute("DROP INDEX IF EXISTS ix_recommendations_published_at;")
    op.execute("DROP INDEX IF EXISTS ix_recommendations_user_id;")
    op.execute("DROP INDEX IF EXISTS ix_recommendations_status;")
    op.execute("DROP INDEX IF EXISTS ix_recommendations_channel_id;")
    op.execute("DROP INDEX IF EXISTS ix_recommendations_asset;")
    op.execute("DROP INDEX IF EXISTS ix_recommendation_events_recommendation_id;")
    op.execute("DROP INDEX IF EXISTS ix_recommendation_events_event_type;")
    op.execute("DROP INDEX IF EXISTS ix_published_messages_recommendation_id;")
    op.execute("DROP INDEX IF EXISTS ix_channels_user_id;")
    op.execute("DROP INDEX IF EXISTS uq_channels_username_ci;")
    op.execute("DROP INDEX IF EXISTS ix_users_telegram_user_id;")
    op.execute("DROP INDEX IF EXISTS ix_users_email;")

    # إسقاط الجداول (مع العلاقات)
    op.execute("DROP TABLE IF EXISTS recommendation_events CASCADE;")
    op.execute("DROP TABLE IF EXISTS published_messages CASCADE;")
    op.execute("DROP TABLE IF EXISTS recommendations CASCADE;")
    op.execute("DROP TABLE IF EXISTS channels CASCADE;")
    op.execute("DROP TABLE IF EXISTS user_roles CASCADE;")
    op.execute("DROP TABLE IF EXISTS roles CASCADE;")
    op.execute("DROP TABLE IF EXISTS users CASCADE;")

    # إسقاط الأنواع
    op.execute("DROP TYPE IF EXISTS exitstrategy;")
    op.execute("DROP TYPE IF EXISTS ordertype;")
    op.execute("DROP TYPE IF EXISTS recommendationstatus;")