"""add shipping records table

Revision ID: add_shipping_records_table
Revises: add_transfer_lpi_tables
Create Date: 2024-03-20 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime

# revision identifiers, used by Alembic.
revision = 'add_shipping_records_table'
down_revision = 'add_transfer_lpi_tables'
branch_labels = None
depends_on = None

def upgrade():
    # Create drawings table if it doesn't exist
    op.create_table('drawings',
        sa.Column('drawing_number', sa.String(length=100), nullable=False),
        sa.Column('sap_id', sa.String(length=100), nullable=False),
        sa.Column('total_quantity', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('drawing_number')
    )
    
    # Create shipping_records table
    op.create_table('shipping_records',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('drawing_number', sa.String(length=100), nullable=False),
        sa.Column('from_machine', sa.String(length=100), nullable=False),
        sa.Column('to_machine', sa.String(length=100), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('notes', sa.String(length=500), nullable=True),
        sa.Column('date', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['drawing_number'], ['drawings.drawing_number'], ),
        sa.ForeignKeyConstraint(['from_machine'], ['machine.name'], ),
        sa.ForeignKeyConstraint(['to_machine'], ['machine.name'], ),
        sa.PrimaryKeyConstraint('id')
    )

def downgrade():
    op.drop_table('shipping_records')
    op.drop_table('drawings') 