"""Add cycle_start_time column to operator_log table

Revision ID: add_cycle_start_time
Revises: 
Create Date: 2024-03-18 10:24:58.000000

"""
from alembic import op
import sqlalchemy as sa
from datetime import timezone

# revision identifiers, used by Alembic.
revision = 'add_cycle_start_time'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    # Add cycle_start_time column with timezone support
    op.add_column('operator_log', sa.Column('cycle_start_time', sa.DateTime(timezone=True), nullable=True))

def downgrade():
    # Remove cycle_start_time column
    op.drop_column('operator_log', 'cycle_start_time') 