"""add layered public and scoped identities

Revision ID: 20251205_add_layered_identity
Revises: 20251204_add_publication_delivery_payload
"""
from collections import defaultdict
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "20251205_add_layered_identity"
down_revision = "20251204_add_publication_delivery_payload"
branch_labels = None
depends_on = None


def _public_ref(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:26].upper()}"


def upgrade() -> None:
    op.create_table(
        "scoped_identity_counters",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("scope_type", sa.String(length=40), nullable=False),
        sa.Column("scope_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_value", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("scope_type", "scope_id", name="uq_identity_counter_scope"),
    )

    op.add_column("users", sa.Column("public_ref", sa.String(length=40), nullable=True))
    op.add_column("users", sa.Column("user_code", sa.String(length=20), nullable=True))
    op.add_column("users", sa.Column("analyst_code", sa.String(length=20), nullable=True))
    op.add_column("recommendations", sa.Column("public_ref", sa.String(length=40), nullable=True))
    op.add_column("recommendations", sa.Column("analyst_sequence", sa.Integer(), nullable=True))
    op.add_column("user_trades", sa.Column("public_ref", sa.String(length=40), nullable=True))
    op.add_column("user_trades", sa.Column("trader_sequence", sa.Integer(), nullable=True))

    bind = op.get_bind()

    users = bind.execute(sa.text("SELECT id, user_type FROM users ORDER BY id")).mappings().all()
    analyst_counter = 1
    for user_number, row in enumerate(users, start=1):
        analyst_code = None
        if str(row["user_type"]).upper().endswith("ANALYST"):
            analyst_code = f"AN-{analyst_counter:06d}"
            analyst_counter += 1
        bind.execute(
            sa.text(
                "UPDATE users SET public_ref=:public_ref, user_code=:user_code, "
                "analyst_code=:analyst_code WHERE id=:id"
            ),
            {
                "id": row["id"],
                "public_ref": _public_ref("USR"),
                "user_code": f"USR-{user_number:06d}",
                "analyst_code": analyst_code,
            },
        )

    recommendation_sequences = defaultdict(int)
    recommendations = bind.execute(
        sa.text("SELECT id, analyst_id FROM recommendations ORDER BY analyst_id, created_at, id")
    ).mappings().all()
    for row in recommendations:
        recommendation_sequences[row["analyst_id"]] += 1
        bind.execute(
            sa.text(
                "UPDATE recommendations SET public_ref=:public_ref, analyst_sequence=:sequence "
                "WHERE id=:id"
            ),
            {
                "id": row["id"],
                "public_ref": _public_ref("REC"),
                "sequence": recommendation_sequences[row["analyst_id"]],
            },
        )

    trade_sequences = defaultdict(int)
    trades = bind.execute(
        sa.text("SELECT id, user_id FROM user_trades ORDER BY user_id, created_at, id")
    ).mappings().all()
    for row in trades:
        trade_sequences[row["user_id"]] += 1
        bind.execute(
            sa.text(
                "UPDATE user_trades SET public_ref=:public_ref, trader_sequence=:sequence "
                "WHERE id=:id"
            ),
            {
                "id": row["id"],
                "public_ref": _public_ref("TRD"),
                "sequence": trade_sequences[row["user_id"]],
            },
        )

    counter_rows = []
    counter_rows.append({"scope_type": "USER_CODE", "scope_id": 0, "next_value": len(users) + 1})
    counter_rows.append({"scope_type": "ANALYST_CODE", "scope_id": 0, "next_value": analyst_counter})
    counter_rows.extend(
        {
            "scope_type": "ANALYST_RECOMMENDATION",
            "scope_id": analyst_id,
            "next_value": sequence + 1,
        }
        for analyst_id, sequence in recommendation_sequences.items()
    )
    counter_rows.extend(
        {
            "scope_type": "TRADER_TRADE",
            "scope_id": user_id,
            "next_value": sequence + 1,
        }
        for user_id, sequence in trade_sequences.items()
    )
    for row in counter_rows:
        bind.execute(
            sa.text(
                "INSERT INTO scoped_identity_counters(scope_type, scope_id, next_value) "
                "VALUES (:scope_type, :scope_id, :next_value)"
            ),
            row,
        )

    op.create_index("ix_users_public_ref", "users", ["public_ref"], unique=True)
    op.create_index("ix_users_user_code", "users", ["user_code"], unique=True)
    op.create_index("ix_users_analyst_code", "users", ["analyst_code"], unique=True)
    op.create_index("ix_recommendations_public_ref", "recommendations", ["public_ref"], unique=True)
    op.create_index(
        "uq_recommendations_analyst_sequence",
        "recommendations",
        ["analyst_id", "analyst_sequence"],
        unique=True,
    )
    op.create_index("ix_user_trades_public_ref", "user_trades", ["public_ref"], unique=True)
    op.create_index(
        "uq_user_trades_trader_sequence",
        "user_trades",
        ["user_id", "trader_sequence"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_user_trades_trader_sequence", table_name="user_trades")
    op.drop_index("ix_user_trades_public_ref", table_name="user_trades")
    op.drop_index("uq_recommendations_analyst_sequence", table_name="recommendations")
    op.drop_index("ix_recommendations_public_ref", table_name="recommendations")
    op.drop_index("ix_users_analyst_code", table_name="users")
    op.drop_index("ix_users_user_code", table_name="users")
    op.drop_index("ix_users_public_ref", table_name="users")
    op.drop_column("user_trades", "trader_sequence")
    op.drop_column("user_trades", "public_ref")
    op.drop_column("recommendations", "analyst_sequence")
    op.drop_column("recommendations", "public_ref")
    op.drop_column("users", "analyst_code")
    op.drop_column("users", "user_code")
    op.drop_column("users", "public_ref")
    op.drop_table("scoped_identity_counters")
