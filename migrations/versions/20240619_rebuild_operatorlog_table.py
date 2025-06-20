"""rebuild operator_log table with batch columns

Revision ID: 20240619_rebuild_operatorlog_table
Revises: 
Create Date: 2025-06-19
"""
from alembic import op
import sqlalchemy as sa

def upgrade():
    op.drop_table('operator_log')
    op.create_table(
        'operator_log',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('drawing_number', sa.String(100)),
        sa.Column('drawing_id', sa.Integer),
        sa.Column('batch_id', sa.String(100)),
        sa.Column('batch_quantity', sa.Integer, default=0),
        sa.Column('setup_start_time', sa.DateTime),
        sa.Column('setup_end_time', sa.DateTime),
        sa.Column('first_cycle_start_time', sa.DateTime),
        sa.Column('last_cycle_end_time', sa.DateTime),
        sa.Column('cycle_start_time', sa.DateTime),
        sa.Column('current_status', sa.String(50)),
        sa.Column('setup_time', sa.Float),
        sa.Column('cycle_time', sa.Float),
        sa.Column('standard_setup_time', sa.Float, default=0),
        sa.Column('standard_cycle_time', sa.Float, default=0),
        sa.Column('abort_reason', sa.Text),
        sa.Column('notes', sa.Text),
        sa.Column('created_at', sa.DateTime),
        sa.Column('updated_at', sa.DateTime),
        sa.Column('batch_number', sa.String(50)),
        sa.Column('run_planned_quantity', sa.Integer, default=0),
        sa.Column('run_completed_quantity', sa.Integer, default=0),
        sa.Column('run_rejected_quantity_fpi', sa.Integer, default=0),
        sa.Column('run_rejected_quantity_lpi', sa.Integer, default=0),
        sa.Column('run_rework_quantity_fpi', sa.Integer, default=0),
        sa.Column('run_rework_quantity_lpi', sa.Integer, default=0),
        sa.Column('quality_status', sa.String(20)),
        sa.Column('operator_id', sa.String(50)),
        sa.Column('machine_id', sa.String(50)),
        sa.Column('shift', sa.String(20)),
        sa.Column('fpi_status', sa.String(20), default='Pending'),
        sa.Column('fpi_timestamp', sa.DateTime),
        sa.Column('fpi_inspector', sa.String(50)),
        sa.Column('lpi_status', sa.String(20)),
        sa.Column('lpi_timestamp', sa.DateTime),
        sa.Column('lpi_inspector', sa.String(50)),
        sa.Column('production_hold_fpi', sa.Boolean, default=True),
        sa.Column('drawing_revision', sa.String(20)),
        sa.Column('sap_id', sa.String(100)),
        sa.Column('end_product_sap_id', sa.String(50)),
        sa.Column('operator_session_id', sa.Integer),
        sa.Column('downtime_minutes', sa.Float, default=0),
        sa.Column('downtime_category', sa.String(20)),
        sa.Column('oee', sa.Float, default=0),
        sa.Column('availability', sa.Float, default=0),
        sa.Column('performance', sa.Float, default=0),
        sa.Column('quality_rate', sa.Float, default=0),
        sa.Column('total_runtime', sa.Float, default=0),
        sa.Column('actual_runtime', sa.Float, default=0),
        sa.Column('ideal_cycle_time', sa.Float),
        sa.Column('actual_cycle_time', sa.Float),
        sa.Column('total_parts_target', sa.Integer, default=0),
        sa.Column('total_parts_produced', sa.Integer, default=0),
        sa.Column('good_parts', sa.Integer, default=0),
        sa.Column('scrap_parts', sa.Integer, default=0),
        sa.Column('rework_parts', sa.Integer, default=0),
        sa.Column('mtbf', sa.Float, default=0),
        sa.Column('mttr', sa.Float, default=0)
    )

def downgrade():
    op.drop_table('operator_log') 