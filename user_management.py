from collections import OrderedDict
import threading
from datetime import datetime, timezone, timedelta
from flask import current_app

# Concurrent user management
active_sessions = OrderedDict()
session_lock = threading.Lock()
MAX_CONCURRENT_USERS = 11  # Increased to 11 as requested

def get_active_sessions():
    """Get current active sessions count after cleaning expired ones"""
    with session_lock:
        current_time = datetime.now(timezone.utc)
        # Clean expired sessions
        for sid, data in list(active_sessions.items()):
            if current_time - data['last_active'] > timedelta(minutes=30):
                active_sessions.pop(sid)
        return len(active_sessions)

def add_user_session(session_id, user_id):
    """Add a new user session"""
    with session_lock:
        active_sessions[session_id] = {
            'user_id': user_id,
            'last_active': datetime.now(timezone.utc)
        }
        return True

def remove_user_session(session_id):
    """Remove a user session"""
    with session_lock:
        if session_id in active_sessions:
            active_sessions.pop(session_id)
            return True
    return False

def update_session_activity(session_id):
    """Update last activity time for a session"""
    with session_lock:
        if session_id in active_sessions:
            active_sessions[session_id]['last_active'] = datetime.now(timezone.utc)
            return True
    return False

def can_add_user():
    """Check if a new user can be added"""
    current_sessions = get_active_sessions()
    return current_sessions < MAX_CONCURRENT_USERS 