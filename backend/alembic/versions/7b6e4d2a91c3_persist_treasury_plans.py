"""persist treasury plan evaluations

Revision ID: 7b6e4d2a91c3
Revises: 203a7fc92e55
Create Date: 2026-10-20
"""
from alembic import op
import sqlalchemy as sa

revision = "7b6e4d2a91c3"
down_revision = "203a7fc92e55"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("treasury_plans",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("data_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("balance_snapshot", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_treasury_plans_created_at", "treasury_plans", ["created_at"], unique=False)
    op.create_index("ix_treasury_plans_status", "treasury_plans", ["status"], unique=False)

def downgrade():
    op.drop_index("ix_treasury_plans_status", table_name="treasury_plans")
    op.drop_index("ix_treasury_plans_created_at", table_name="treasury_plans")
    op.drop_table("treasury_plans")
