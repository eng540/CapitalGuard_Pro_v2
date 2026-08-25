"""Repair the PostgreSQL sequence used by historical_market_evidence.id.

The repair is intentionally forward-only: it moves the sequence to the first
unused id and never changes existing rows. SQLite and other non-PostgreSQL
dialects are left untouched because they do not expose PostgreSQL sequences.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260825_repair_historical_market_evidence_sequence"
down_revision = "20260825_repair_historical_attribution_review_state"
branch_labels = None
depends_on = None

_TABLE = "historical_market_evidence"
_COLUMN = "id"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    inspector = sa.inspect(bind)
    if _TABLE not in inspector.get_table_names():
        return

    sequence_name = bind.execute(
        sa.text("SELECT pg_get_serial_sequence(:table_name, :column_name)"),
        {"table_name": _TABLE, "column_name": _COLUMN},
    ).scalar()
    if not sequence_name:
        return

    bind.execute(
        sa.text(
            "SELECT setval(CAST(:sequence_name AS regclass), "
            "COALESCE((SELECT MAX(id) FROM historical_market_evidence), 0) + 1, false)"
        ),
        {"sequence_name": sequence_name},
    )


def downgrade() -> None:
    # Sequence alignment is a corrective operation and is intentionally not
    # reversed: reverting it could recreate duplicate primary-key failures.
    pass
