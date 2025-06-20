"""add batch_id and batch_quantity columns to operator_log and quality_check

Revision ID: 20240619_add_batch_columns_operatorlog_qualitycheck
Revises: 
Create Date: 2025-06-19
"""
from alembic import op
import sqlalchemy as sa

def upgrade():
    # Create Batch table
    op.create_table(
        'batch',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('drawing_id', sa.Integer, sa.ForeignKey('machine_drawing.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True)),
        sa.Column('batch_quantity', sa.Integer, default=0)
    )
    # Change batch_id columns to Integer, FK
    op.alter_column('operator_log', 'batch_id', type_=sa.Integer)
    op.create_foreign_key('fk_operatorlog_batch', 'operator_log', 'batch', ['batch_id'], ['id'])
    op.alter_column('quality_check', 'batch_id', type_=sa.Integer)
    op.create_foreign_key('fk_qualitycheck_batch', 'quality_check', 'batch', ['batch_id'], ['id'])
    # Data migration: (pseudo-code, real migration may need manual intervention)
    # For each unique old batch_id string, create a Batch row and update logs/quality_checks
    # (If not possible, set batch_id to NULL for old logs)

def downgrade():
    op.drop_constraint('fk_operatorlog_batch', 'operator_log', type_='foreignkey')
    op.drop_constraint('fk_qualitycheck_batch', 'quality_check', type_='foreignkey')
    op.alter_column('operator_log', 'batch_id', type_=sa.String(100))
    op.alter_column('quality_check', 'batch_id', type_=sa.String(100))
    op.drop_table('batch') 