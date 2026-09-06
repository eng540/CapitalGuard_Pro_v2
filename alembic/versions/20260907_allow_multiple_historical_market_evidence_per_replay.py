"""Allow multiple market-evidence artifacts for one replay run."""

from alembic import op
import sqlalchemy as sa


revision = "20260907_allow_multiple_historical_market_evidence_per_replay"
down_revision = "20260828_add_historical_replay_coverage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("historical_market_evidence", recreate="always") as batch_op:
        batch_op.alter_column(
            "replay_run_ref",
            existing_type=sa.String(length=48),
            existing_nullable=False,
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("historical_market_evidence", recreate="always") as batch_op:
        batch_op.alter_column(
            "replay_run_ref",
            existing_type=sa.String(length=48),
            existing_nullable=False,
            unique=True,
        )
