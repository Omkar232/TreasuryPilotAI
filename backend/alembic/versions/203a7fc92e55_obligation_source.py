"""add obligation provenance and lookup index

Revision ID: 203a7fc92e55
Revises: 952aa0c7137c
Create Date: 2026-10-20
"""
from alembic import op
import sqlalchemy as sa

revision = "203a7fc92e55"
down_revision = "952aa0c7137c"
branch_labels = None
depends_on = None

def upgrade():
    with op.batch_alter_table("obligations") as batch:
        batch.add_column(sa.Column("source", sa.String(length=32), nullable=True))
    op.execute("UPDATE obligations SET source = CASE WHEN simulated THEN 'synthetic_demo_fixture' ELSE 'operator_entered' END")
    with op.batch_alter_table("obligations") as batch:
        batch.alter_column("source", existing_type=sa.String(length=32), nullable=False,
            server_default="operator_entered")
    op.create_index("ix_obligations_status_due", "obligations", ["status", "due_at"], unique=False)

def downgrade():
    op.drop_index("ix_obligations_status_due", table_name="obligations")
    with op.batch_alter_table("obligations") as batch:
        batch.drop_column("source")
