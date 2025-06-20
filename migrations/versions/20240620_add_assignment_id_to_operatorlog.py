"""
Add assignment_id to operator_log
"""
from alembic import op
import sqlalchemy as sa

def upgrade():
    op.add_column('operator_log', sa.Column('assignment_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_operatorlog_assignment',
        'operator_log', 'machine_drawing_assignment',
        ['assignment_id'], ['id'],
        ondelete='SET NULL'
    )

def downgrade():
    op.drop_constraint('fk_operatorlog_assignment', 'operator_log', type_='foreignkey')
    op.drop_column('operator_log', 'assignment_id') 