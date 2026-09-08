"""Re-align historical_market_evidence.id after table recreation.

The earlier sequence repair runs before later migrations.  A migration that
recreates historical_market_evidence can preserve explicit primary-key values
while leaving the owned PostgreSQL sequence behind the current MAX(id).
Align it again at the end of the migration chain so new ORM inserts cannot
collide with retained evidence rows.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260908_repair_evidence_seq"
down_revision = "20260907_allow_multiple_historical_market_evidence_per_replay"
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
    # Sequence alignment is corrective and intentionally not reversed.
    pass
