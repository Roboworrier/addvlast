"""add transfer and lpi tables

Revision ID: add_transfer_lpi_tables
Create Date: 2024-02-20
"""
from alembic import op
import sqlalchemy as sa

def upgrade():
    # Create transfer_requests table
    op.create_table('transfer_requests',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('route_operation_id', sa.Integer()),
        sa.Column('from_machine', sa.String(50)),
        sa.Column('to_machine', sa.String(50)),
        sa.Column('quantity', sa.Integer()),
        sa.Column('sap_id', sa.String(50)),
        sa.Column('status', sa.String(50)),
        sa.Column('created_at', sa.DateTime()),
        sa.Column('completed_at', sa.DateTime()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['route_operation_id'], ['route_operations.id'])
    )

    # Create lpi_records table
    op.create_table('lpi_records',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('transfer_id', sa.Integer()),
        sa.Column('inspector', sa.String(50)),
        sa.Column('inspection_date', sa.DateTime()),
        sa.Column('result', sa.String(50)),
        sa.Column('remarks', sa.Text),
        sa.Column('dimensions_ok', sa.Boolean),
        sa.Column('surface_finish_ok', sa.Boolean),
        sa.Column('critical_features_ok', sa.Boolean),
        sa.Column('measurements', sa.JSON),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['transfer_id'], ['transfer_requests.id'])
    )

def downgrade():
    op.drop_table('lpi_records')
    op.drop_table('transfer_requests') 