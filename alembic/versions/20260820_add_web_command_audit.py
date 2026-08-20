"""Add audited idempotency ledger for privileged Web commands.

Revision ID: 20260820_add_web_command_audit
Revises: 20251216_add_temporal_forward_decisions
"""

from alembic import op
import sqlalchemy as sa


revision = "20260820_add_web_command_audit"
down_revision = "20251216_add_temporal_forward_decisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "web_command_audit",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("command_type", sa.String(length=64), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("target_type", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="COMPLETED"),
        sa.Column("response_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_web_command_audit_idempotency_key"),
    )
    op.create_index("ix_web_command_audit_command_type", "web_command_audit", ["command_type"])
    op.create_index("ix_web_command_audit_actor_user_id", "web_command_audit", ["actor_user_id"])
    op.create_index("ix_web_command_audit_target_id", "web_command_audit", ["target_id"])
    op.create_index("ix_web_command_audit_status", "web_command_audit", ["status"])


def downgrade() -> None:
    op.drop_index("ix_web_command_audit_status", table_name="web_command_audit")
    op.drop_index("ix_web_command_audit_target_id", table_name="web_command_audit")
    op.drop_index("ix_web_command_audit_actor_user_id", table_name="web_command_audit")
    op.drop_index("ix_web_command_audit_command_type", table_name="web_command_audit")
    op.drop_table("web_command_audit")
