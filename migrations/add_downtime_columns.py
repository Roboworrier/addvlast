"""Add downtime columns to operator_log table

Revision ID: add_downtime_columns
"""
from alembic import op
import sqlalchemy as sa

def upgrade():
    # Add downtime_minutes and downtime_category columns
    op.add_column('operator_log', sa.Column('downtime_minutes', sa.Float(), nullable=True, server_default='0'))
    op.add_column('operator_log', sa.Column('downtime_category', sa.String(20), nullable=True))

def downgrade():
    # Remove the columns
    op.drop_column('operator_log', 'downtime_minutes')
    op.drop_column('operator_log', 'downtime_category') 