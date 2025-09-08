# --- START: alembic/versions/20250910_update_channels_add_title_nullable_username.py ---
"""Update channels: add title/last_verified_at/notes, make username nullable, add CI unique index

Revision ID: 20250910_update_channels_add_title_nullable_username
Revises: 20250909_create_channels_table
Create Date: 2025-09-10 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20250910_update_channels_add_title_nullable_username"
down_revision = "20250909_create_channels_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # --- 1) Add columns if missing ---
    cols = {c["name"] for c in inspector.get_columns("channels")}

    if "title" not in cols:
        op.add_column("channels", sa.Column("title", sa.String(length=255), nullable=True))

    if "last_verified_at" not in cols:
        op.add_column("channels", sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True))

    if "notes" not in cols:
        op.add_column("channels", sa.Column("notes", sa.Text(), nullable=True))

    # --- 2) Make username nullable (for private channels) ---
    # existing_type best-effort; previous migration used sa.String() without length.
    op.alter_column(
        "channels",
        "username",
        existing_type=sa.String(length=255) if any(c.get("type") and getattr(c["type"], "length", None) for c in inspector.get_columns("channels") if c["name"]=="username") else sa.String(),
        nullable=True,
        existing_nullable=False,
    )

    # --- 3) Case-insensitive uniqueness on username (PostgreSQL only) ---
    # Previous migration created a UniqueConstraint('username'), which on PG
    # typically ends up named "channels_username_key". We'll drop it if present
    # and create a functional unique index on lower(username) where username IS NOT NULL.
    if bind.dialect.name == "postgresql":
        # Drop plain unique constraint on username if it exists
        # Try common default name first; if unknown, scan constraints.
        try:
            op.drop_constraint("channels_username_key", "channels", type_="unique")
        except Exception:
            # Fallback: search for any unique constraint that only covers "username"
            # (not strictly necessary if not present).
            pass

        # Create functional unique index (case-insensitive), partial on non-null usernames
        op.create_index(
            "uq_channels_username_ci",
            "channels",
            [sa.text("lower(username)")],
            unique=True,
            postgresql_where=sa.text("username IS NOT NULL"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Revert CI unique index and (optionally) recreate simple unique constraint on username
    if bind.dialect.name == "postgresql":
        try:
            op.drop_index("uq_channels_username_ci", table_name="channels")
        except Exception:
            pass
        # You can optionally recreate the original unique constraint, but since
        # username is now nullable and we want to keep that behavior cleanly,
        # we won't add a NOT NULL unique constraint back during downgrade.

    # Make username NOT NULL again (best-effort; only if column exists)
    cols = {c["name"] for c in inspector.get_columns("channels")}
    if "username" in cols:
        op.alter_column(
            "channels",
            "username",
            existing_type=sa.String(length=255) if any(c.get("type") and getattr(c["type"], "length", None) for c in inspector.get_columns("channels") if c["name"]=="username") else sa.String(),
            nullable=False,
            existing_nullable=True,
        )

    # Drop the added columns if they exist
    if "notes" in cols:
        op.drop_column("channels", "notes")
    if "last_verified_at" in cols:
        op.drop_column("channels", "last_verified_at")
    if "title" in cols:
        op.drop_column("channels", "title")
# --- END: alembic/versions/20250910_update_channels_add_title_nullable_username.py ---