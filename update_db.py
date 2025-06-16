from app import app, db
from sqlalchemy import text
import os
from datetime import datetime

def update_database():
    with app.app_context():
        try:
            # Create backup first
            if os.path.exists('digital_twin.db'):
                backup_name = f'digital_twin_backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.db'
                os.system(f'copy digital_twin.db {backup_name}')
                print(f"Created backup: {backup_name}")

            # Add new columns
            db.session.execute(text("""
                ALTER TABLE scrap_log 
                ADD COLUMN scrapped_at DATETIME;
            """))
            
            db.session.execute(text("""
                ALTER TABLE scrap_log 
                ADD COLUMN scrapped_by VARCHAR(100);
            """))

            # Update existing records
            db.session.execute(text("""
                UPDATE scrap_log 
                SET scrapped_at = created_at 
                WHERE scrapped_at IS NULL;
            """))

            # Rename quantity column to quantity_scrapped
            db.session.execute(text("""
                ALTER TABLE scrap_log 
                RENAME COLUMN quantity TO quantity_scrapped;
            """))

            db.session.commit()
            print("Database updated successfully!")
            
        except Exception as e:
            db.session.rollback()
            print(f"Error updating database: {str(e)}")
            raise

if __name__ == '__main__':
    update_database() 