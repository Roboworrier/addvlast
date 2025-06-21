#!/usr/bin/env python3
"""
Test script to verify database schema and connectivity
"""
import os
import sys
from app import app, db
from models import OperatorLog

def test_database():
    with app.app_context():
        try:
            # Test basic query
            count = OperatorLog.query.count()
            print(f"Total operator_log records: {count}")
            
            # Test query with assignment_id
            logs = OperatorLog.query.limit(5).all()
            print(f"Retrieved {len(logs)} logs")
            
            for log in logs:
                print(f"Log ID: {log.id}, Assignment ID: {log.assignment_id}")
            
            print("Database test successful!")
            return True
            
        except Exception as e:
            print(f"Database test failed: {str(e)}")
            return False

if __name__ == "__main__":
    success = test_database()
    sys.exit(0 if success else 1) 