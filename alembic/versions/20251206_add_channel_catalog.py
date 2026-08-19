"""add canonical channel catalog

Revision ID: 20251206_add_channel_catalog
Revises: 20251205_add_layered_identity
"""
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "20251206_add_channel_catalog"
down_revision = "20251205_add_layered_identity"
branch_labels = None
depends_on = None


def _public_ref() -> str:
    return f"CH-{uuid4().hex[:26].upper()}"


def upgrade() -> None:
    op.create_table(
        "channel_catalog",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("public_ref", sa.String(length=40), nullable=True),
        sa.Column("channel_code", sa.String(length=20), nullable=True),
        sa.Column("telegram_channel_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.add_column("channels", sa.Column("channel_catalog_id", sa.Integer(), nullable=True))
    op.add_column("watched_channels", sa.Column("channel_catalog_id", sa.Integer(), nullable=True))

    bind = op.get_bind()
    channel_ids = bind.execute(
        sa.text(
            "SELECT telegram_channel_id, MAX(title) AS title FROM channels "
            "GROUP BY telegram_channel_id ORDER BY telegram_channel_id"
        )
    ).mappings().all()
    watched_ids = bind.execute(
        sa.text(
            "SELECT telegram_channel_id, MAX(channel_title) AS title FROM watched_channels "
            "GROUP BY telegram_channel_id ORDER BY telegram_channel_id"
        )
    ).mappings().all()

    known = {}
    for row in channel_ids + watched_ids:
        telegram_id = row["telegram_channel_id"]
        if telegram_id not in known:
            known[telegram_id] = row["title"]
        elif not known[telegram_id] and row["title"]:
            known[telegram_id] = row["title"]

    catalog_by_tg = {}
    for sequence, (telegram_id, title) in enumerate(sorted(known.items()), start=1):
        result = bind.execute(
            sa.text(
                "INSERT INTO channel_catalog(public_ref, channel_code, telegram_channel_id, title, is_active) "
                "VALUES (:public_ref, :channel_code, :telegram_channel_id, :title, :is_active)"
            ),
            {
                "public_ref": _public_ref(),
                "channel_code": f"CH-{sequence:06d}",
                "telegram_channel_id": telegram_id,
                "title": title,
                "is_active": True,
            },
        )
        # SQLite and PostgreSQL both expose the generated integer key this way.
        catalog_by_tg[telegram_id] = bind.execute(
            sa.text("SELECT id FROM channel_catalog WHERE telegram_channel_id=:telegram_channel_id"),
            {"telegram_channel_id": telegram_id},
        ).scalar_one()

    for telegram_id, catalog_id in catalog_by_tg.items():
        bind.execute(
            sa.text("UPDATE channels SET channel_catalog_id=:catalog_id WHERE telegram_channel_id=:telegram_id"),
            {"catalog_id": catalog_id, "telegram_id": telegram_id},
        )
        bind.execute(
            sa.text("UPDATE watched_channels SET channel_catalog_id=:catalog_id WHERE telegram_channel_id=:telegram_id"),
            {"catalog_id": catalog_id, "telegram_id": telegram_id},
        )

    bind.execute(
        sa.text(
            "INSERT INTO scoped_identity_counters(scope_type, scope_id, next_value) "
            "VALUES ('CHANNEL_CODE', 0, :next_value)"
        ),
        {"next_value": len(catalog_by_tg) + 1},
    )

    op.create_index("ix_channel_catalog_public_ref", "channel_catalog", ["public_ref"], unique=True)
    op.create_index("ix_channel_catalog_channel_code", "channel_catalog", ["channel_code"], unique=True)
    op.create_index("ix_channel_catalog_telegram_id", "channel_catalog", ["telegram_channel_id"], unique=True)
    op.create_index("ix_channels_channel_catalog_id", "channels", ["channel_catalog_id"])
    op.create_index("ix_watched_channels_channel_catalog_id", "watched_channels", ["channel_catalog_id"])


def downgrade() -> None:
    op.drop_index("ix_watched_channels_channel_catalog_id", table_name="watched_channels")
    op.drop_index("ix_channels_channel_catalog_id", table_name="channels")
    op.drop_index("ix_channel_catalog_telegram_id", table_name="channel_catalog")
    op.drop_index("ix_channel_catalog_channel_code", table_name="channel_catalog")
    op.drop_index("ix_channel_catalog_public_ref", table_name="channel_catalog")
    op.drop_column("watched_channels", "channel_catalog_id")
    op.drop_column("channels", "channel_catalog_id")
    op.drop_table("channel_catalog")
