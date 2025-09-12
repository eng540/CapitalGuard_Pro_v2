# --- START OF FINAL, COPY COD FILE: alembic/versions/20250912_add_exit_strategy_and_profit_stop.py ---
"""add exit_strategy and profit_stop

Revision ID: 20250912_add_exit_strategy_and_profit_stop
Revises: a20250911_events_tracking_safe
Create Date: 2025-09-12 00:00:00

"""
from alembic import op
import sqlalchemy as sa

# ========= Alembic IDs =========
revision = "20250912_add_exit_strategy_and_profit_stop"
down_revision = "a20250911_events_tracking_safe"
branch_labels = None
depends_on = None
# =================================

# Define the new Enum type for PostgreSQL
exit_strategy_enum = sa.Enum(
    "CLOSE_AT_FINAL_TP",
    "MANUAL_CLOSE_ONLY",
    name="exitstrategy",
)

def _table_exists(bind, table_name: str) -> bool:
    insp = sa.inspect(bind)
    return table_name in insp.get_table_names()

def _column_exists(bind, table_name: str, column_name: str) -> bool:
    insp = sa.inspect(bind)
    try:
        cols = {c["name"] for c in insp.get_columns(table_name)}
    except Exception:
        return False
    return column_name in cols

def _unique_exists(bind, table_name: str, constraint_name: str) -> bool:
    insp = sa.inspect(bind)
    try:
        uqs = insp.get_unique_constraints(table_name)
    except Exception:
        return False
    names = {u.get("name") for u in uqs if u.get("name")}
    return constraint_name in names


def upgrade() -> None:
    bind = op.get_bind()

    # --- Step 1: Create ENUM type (PostgreSQL) ---
    exit_strategy_enum.create(bind, checkfirst=True)

    # --- Step 2: Add new columns to recommendations ---
    if _table_exists(bind, "recommendations"):
        with op.batch_alter_table("recommendations", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "exit_strategy",
                    exit_strategy_enum,
                    nullable=False,
                    server_default="CLOSE_AT_FINAL_TP",
                )
            )
            batch_op.add_column(sa.Column("profit_stop_price", sa.Float(), nullable=True))

    # --- Step 3: Optional unique constraint ---
    if _table_exists(bind, "recommendations") and _column_exists(bind, "recommendations", "analyst_rec_id"):
        if not _unique_exists(bind, "recommendations", "uq_user_analyst_rec_id"):
            op.create_unique_constraint(
                "uq_user_analyst_rec_id",
                "recommendations",
                ["user_id", "analyst_rec_id"],
            )


def downgrade() -> None:
    bind = op.get_bind()

    # Drop unique constraint if exists
    if _table_exists(bind, "recommendations") and _unique_exists(bind, "recommendations", "uq_user_analyst_rec_id"):
        try:
            op.drop_constraint("uq_user_analyst_rec_id", "recommendations", type_="unique")
        except Exception:
            pass

    # Drop added columns
    if _table_exists(bind, "recommendations"):
        with op.batch_alter_table("recommendations", schema=None) as batch_op:
            if _column_exists(bind, "recommendations", "profit_stop_price"):
                batch_op.drop_column("profit_stop_price")
            if _column_exists(bind, "recommendations", "exit_strategy"):
                batch_op.drop_column("exit_strategy")

    # Drop ENUM type
    try:
        exit_strategy_enum.drop(bind, checkfirst=True)
    except Exception:
        pass
# --- END OF FINAL, COPY COD FILE: alembic/versions/20250912_add_exit_strategy_and_profit_stop.py ---