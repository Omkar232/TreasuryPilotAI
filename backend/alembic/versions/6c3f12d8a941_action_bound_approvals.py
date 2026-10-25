"""bind approvals to durable transfer operations

Revision ID: 6c3f12d8a941
Revises: 44bd219aa653
Create Date: 2026-10-20
"""
from alembic import op
import sqlalchemy as sa


revision = "6c3f12d8a941"
down_revision = "44bd219aa653"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("approvals") as batch_op:
        batch_op.add_column(sa.Column("operation_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("requested_by", sa.String(length=320), nullable=True))
        batch_op.add_column(sa.Column("policy_version", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_approvals_operation_id", "agent_executions", ["operation_id"], ["id"])
        batch_op.create_index("ix_approvals_operation_id", ["operation_id"], unique=False)


def downgrade():
    with op.batch_alter_table("approvals") as batch_op:
        batch_op.drop_index("ix_approvals_operation_id")
        batch_op.drop_constraint("fk_approvals_operation_id", type_="foreignkey")
        batch_op.drop_column("policy_version")
        batch_op.drop_column("requested_by")
        batch_op.drop_column("operation_id")
