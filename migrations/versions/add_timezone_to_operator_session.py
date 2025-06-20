"""add timezone to operator session

Revision ID: add_timezone_to_operator_session
Revises: add_cycle_start_time
Create Date: 2024-03-18 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime, timezone

# revision identifiers, used by Alembic.
revision = 'add_timezone_to_operator_session'
down_revision = 'add_cycle_start_time'
branch_labels = None
depends_on = None

def upgrade():
    # Convert existing datetime columns to timezone-aware
    with op.batch_alter_table('operator_session') as batch_op:
        # Temporarily rename existing columns
        batch_op.alter_column('login_time',
            new_column_name='login_time_old',
            existing_type=sa.DateTime()
        )
        batch_op.alter_column('logout_time',
            new_column_name='logout_time_old',
            existing_type=sa.DateTime()
        )
        batch_op.alter_column('start_time',
            new_column_name='start_time_old',
            existing_type=sa.DateTime()
        )
        batch_op.alter_column('end_time',
            new_column_name='end_time_old',
            existing_type=sa.DateTime()
        )
        
        # Add new timezone-aware columns
        batch_op.add_column(sa.Column('login_time', sa.DateTime(timezone=True)))
        batch_op.add_column(sa.Column('logout_time', sa.DateTime(timezone=True)))
        batch_op.add_column(sa.Column('start_time', sa.DateTime(timezone=True)))
        batch_op.add_column(sa.Column('end_time', sa.DateTime(timezone=True)))

    # Update data - convert naive datetimes to UTC
    connection = op.get_bind()
    connection.execute(
        """
        UPDATE operator_session 
        SET login_time = login_time_old AT TIME ZONE 'UTC',
            logout_time = CASE 
                WHEN logout_time_old IS NOT NULL 
                THEN logout_time_old AT TIME ZONE 'UTC'
                ELSE NULL
            END,
            start_time = start_time_old AT TIME ZONE 'UTC',
            end_time = CASE 
                WHEN end_time_old IS NOT NULL 
                THEN end_time_old AT TIME ZONE 'UTC'
                ELSE NULL
            END
        """
    )

    # Drop old columns
    with op.batch_alter_table('operator_session') as batch_op:
        batch_op.drop_column('login_time_old')
        batch_op.drop_column('logout_time_old')
        batch_op.drop_column('start_time_old')
        batch_op.drop_column('end_time_old')

def downgrade():
    # Convert timezone-aware columns back to naive datetime
    with op.batch_alter_table('operator_session') as batch_op:
        # Temporarily rename existing columns
        batch_op.alter_column('login_time',
            new_column_name='login_time_old',
            existing_type=sa.DateTime(timezone=True)
        )
        batch_op.alter_column('logout_time',
            new_column_name='logout_time_old',
            existing_type=sa.DateTime(timezone=True)
        )
        batch_op.alter_column('start_time',
            new_column_name='start_time_old',
            existing_type=sa.DateTime(timezone=True)
        )
        batch_op.alter_column('end_time',
            new_column_name='end_time_old',
            existing_type=sa.DateTime(timezone=True)
        )
        
        # Add new naive datetime columns
        batch_op.add_column(sa.Column('login_time', sa.DateTime()))
        batch_op.add_column(sa.Column('logout_time', sa.DateTime()))
        batch_op.add_column(sa.Column('start_time', sa.DateTime()))
        batch_op.add_column(sa.Column('end_time', sa.DateTime()))

    # Update data - convert UTC to naive datetime
    connection = op.get_bind()
    connection.execute(
        """
        UPDATE operator_session 
        SET login_time = login_time_old AT TIME ZONE 'UTC',
            logout_time = CASE 
                WHEN logout_time_old IS NOT NULL 
                THEN logout_time_old AT TIME ZONE 'UTC'
                ELSE NULL
            END,
            start_time = start_time_old AT TIME ZONE 'UTC',
            end_time = CASE 
                WHEN end_time_old IS NOT NULL 
                THEN end_time_old AT TIME ZONE 'UTC'
                ELSE NULL
            END
        """
    )

    # Drop old columns
    with op.batch_alter_table('operator_session') as batch_op:
        batch_op.drop_column('login_time_old')
        batch_op.drop_column('logout_time_old')
        batch_op.drop_column('start_time_old')
        batch_op.drop_column('end_time_old') 