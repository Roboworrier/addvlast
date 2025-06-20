"""add route tables

Revision ID: add_route_tables
Create Date: 2024-02-20
"""
from alembic import op
import sqlalchemy as sa

def upgrade():
    # Create process_routes table
    op.create_table('process_routes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('sap_id', sa.String(50), nullable=False),
        sa.Column('total_quantity', sa.Integer(), nullable=False),
        sa.Column('created_by', sa.String(50)),
        sa.Column('created_at', sa.DateTime()),
        sa.Column('status', sa.String(50)),
        sa.Column('deadline', sa.DateTime()),
        sa.PrimaryKeyConstraint('id')
    )

    # Create route_operations table
    op.create_table('route_operations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('route_id', sa.Integer()),
        sa.Column('operation_type', sa.String(50)),
        sa.Column('sequence', sa.Integer()),
        sa.Column('assigned_machine', sa.String(50)),
        sa.Column('status', sa.String(50)),
        sa.Column('completed_quantity', sa.Integer()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['route_id'], ['process_routes.id'])
    )

def downgrade():
    op.drop_table('route_operations')
    op.drop_table('process_routes') 