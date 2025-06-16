from functools import wraps
from flask import current_app
from models import db
from datetime import datetime, timedelta
import threading

# Safe calculation utilities
def safe_division(numerator, denominator, default=0.0):
    """Safely perform division with error handling"""
    try:
        return (numerator / denominator) if denominator != 0 else default
    except (TypeError, ZeroDivisionError):
        return default

# Database transaction management
def with_db_session(func):
    """Decorator to handle database sessions and transactions"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            result = func(*args, **kwargs)
            db.session.commit()
            return result
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Database error in {func.__name__}: {str(e)}")
            raise
        finally:
            db.session.remove()
    return wrapper

# Rate limiting
class RateLimiter:
    def __init__(self, max_requests=10, time_window=60):
        self.max_requests = max_requests
        self.time_window = time_window  # in seconds
        self.requests = {}
        self.lock = threading.Lock()

    def is_allowed(self, client_id):
        now = datetime.now()
        with self.lock:
            # Clean old entries
            self.requests = {k: v for k, v in self.requests.items() 
                           if (now - v[-1]).seconds < self.time_window}
            
            # Check client's requests
            if client_id not in self.requests:
                self.requests[client_id] = [now]
                return True
                
            client_reqs = self.requests[client_id]
            if len(client_reqs) < self.max_requests:
                client_reqs.append(now)
                return True
                
            # Check if oldest request is outside time window
            if (now - client_reqs[0]).seconds >= self.time_window:
                client_reqs.pop(0)
                client_reqs.append(now)
                return True
                
            return False

# Input validation
def validate_production_data(sap_id, quantity):
    """Validate production data (SAP ID and quantity)"""
    return validate_sap_id(sap_id) and validate_quantity(quantity)

def validate_sap_id(sap_id):
    """Validate SAP ID format"""
    if not isinstance(sap_id, str) or not sap_id:
        return False
    # Allow alphanumeric SAP IDs between 3-20 characters
    import re
    return bool(re.match(r'^[A-Za-z0-9-_]{3,20}$', sap_id))

def validate_quantity(quantity):
    """Validate quantity value"""
    try:
        qty = float(quantity)  # Allow float values
        return 0 < qty <= 10000  # Positive and reasonable maximum
    except (TypeError, ValueError):
        return False 