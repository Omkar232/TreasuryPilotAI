"""reserve one recovery attempt for each original transfer

Revision ID: 44bd219aa653
Revises: 7b6e4d2a91c3
Create Date: 2026-10-20
"""
from alembic import op
import sqlalchemy as sa

revision = "44bd219aa653"
down_revision = "7b6e4d2a91c3"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("idempotency_records", sa.Column("original_transfer_id", sa.String(length=200), nullable=True))
    op.create_index("uq_idempotency_original_transfer_id", "idempotency_records",
        ["original_transfer_id"], unique=True)


def downgrade():
    op.drop_index("uq_idempotency_original_transfer_id", table_name="idempotency_records")
    op.drop_column("idempotency_records", "original_transfer_id")
