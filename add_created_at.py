import sqlite3
import os
from datetime import datetime, timezone

def add_created_at_column():
    # Get the database path
    instance_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance')
    db_path = os.path.join(instance_path, 'chipsight.db')

    try:
        # Connect to the database
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Add the created_at column
        cursor.execute('''
            ALTER TABLE end_product 
            ADD COLUMN created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        ''')

        # Update existing records
        now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute('''
            UPDATE end_product 
            SET created_at = ? 
            WHERE created_at IS NULL
        ''', (now,))

        # Commit the changes
        conn.commit()
        print("Successfully added created_at column")

    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e):
            print("Column already exists")
        else:
            print(f"Error: {e}")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        conn.close()

if __name__ == '__main__':
    add_created_at_column() 