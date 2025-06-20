# run_migration.py
from app import app, db
from alembic import command
from alembic.config import Config
import os

def run_migrations():
    # Initialize the Flask app context
    with app.app_context():
        try:
            # Get the Alembic configuration
            alembic_cfg = Config("alembic.ini")
            
            # Create a backup of the database
            if os.path.exists('instance/chipsight.db'):
                backup_name = f'instance/chipsight.db.backup'
                os.system(f'copy instance/chipsight.db {backup_name}')
                print(f"Created backup: {backup_name}")
            
            # Drop all tables
            db.drop_all()
            print("Dropped all existing tables")
            
            # Recreate all tables with the new schema
            db.create_all()
            print("Created all tables with new schema")
            
            # Run all migrations
            command.upgrade(alembic_cfg, "head")
            print("Applied all migrations")
            
            db.session.commit()
            print("Migration completed successfully!")
            
        except Exception as e:
            print(f"Error during migration: {str(e)}")
            raise

if __name__ == "__main__":
    run_migrations()