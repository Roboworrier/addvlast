"""
ChipSight - Digital Twin for Manufacturing
Copyright (c) 2025 Diwakar Singh. All rights reserved.
See COMPANY_LICENSE.md for license terms.

This software and its source code are the exclusive intellectual property of Diwakar Singh.
Unauthorized copying, modification, distribution, or use is strictly prohibited.
"""
import eventlet
eventlet.monkey_patch()
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file, session, make_response, Response, current_app
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta, timezone
import os
import pandas as pd
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import relationship, joinedload
from jinja2 import FileSystemLoader
import sys
from markupsafe import Markup
from sqlalchemy import case, func
from io import BytesIO, StringIO
import logging
from logging.handlers import RotatingFileHandler
import traceback
import shutil
import random
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
import json
import csv
import psutil
import platform
from werkzeug.security import generate_password_hash, check_password_hash
from user_management import can_add_user, add_user_session, remove_user_session
from live_data import get_live_production_data, get_machine_metrics
from utils import safe_division, with_db_session, RateLimiter
from apscheduler.schedulers.background import BackgroundScheduler
import atexit
from flask_migrate import Migrate
import re  # Add this with other imports
from pytz import timezone as pytz_timezone
import threading
import pytz
from scheduled_tasks import init_scheduled_tasks
from live_routes import init_live_routes
from route_management import route_bp
from sqlalchemy import or_
import string
from operator_log_management import operator_log_bp

def utc_to_ist(dt):
    """Convert UTC datetime to IST"""
    if not dt:
        return None
    UTC = pytz.timezone('UTC')
    IST = pytz.timezone('Asia/Kolkata')
    dt = UTC.localize(dt) if dt.tzinfo is None else dt
    return dt.astimezone(IST)

sys.setrecursionlimit(3000)  # Increase recursion limit if needed
for d in ['logs', 'uploads', os.path.join(os.path.expanduser('~'), 'chipsight_backups')]:
    os.makedirs(d, exist_ok=True)

# Disable .env file loading
os.environ['FLASK_SKIP_DOTENV'] = '1'

# Initialize Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key'

# Ensure instance directory exists
instance_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance')
if not os.path.exists(instance_path):
    os.makedirs(instance_path)

# Database configuration
db_path = os.path.join(instance_path, 'chipsight.db')
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Import models and initialize SQLAlchemy
from models import db
db.init_app(app)
migrate = Migrate(app, db)
socketio = SocketIO(app)

# Import models after db is initialized
from models import (
    EndProduct, Project, Machine, OperatorLog, 
    OperatorSession, MachineBreakdownLog,
    QualityCheck, ProductionRecord, MachineDrawing,
    User, SystemLog, ReworkQueue, ScrapLog,
    MachineDrawingAssignment, TransferHistory,
    ProcessRoute, RouteOperation, ShippingRecord,
    Batch
)

# Create tables
with app.app_context():
    db.create_all()
    app.logger.info('Database tables created')
    # Add after app initialization, before routes
UPDATE_INTERVAL = 30  # seconds between updates
ERROR_RETRY_INTERVAL = 60  # seconds to wait after error

# Initialize Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login_general'
login_manager.login_message = 'Please log in to access this page.'
login_manager.login_message_category = 'error'

class SimpleUser(UserMixin):
    def __init__(self, user_id, role):
        self.id = user_id
        self.role = role
    
    @property
    def is_admin(self):
        return self.role == 'admin'
    
    @property
    def is_plant_head(self):
        return self.role == 'plant_head'
    
    @property
    def is_planner(self):
        return self.role == 'planner'
    
    @property
    def is_manager(self):
        return self.role == 'manager'
    
    @property
    def is_quality(self):
        return self.role == 'quality'
    
    @property
    def is_operator(self):
        return self.role == 'operator'

@login_manager.user_loader
def load_user(user_id):
    # For SimpleUser format: "{name}|{role}"
    if '|' in user_id:
        name, role = user_id.split('|')
        return SimpleUser(user_id, role)
    
    # For database User model
    try:
        return User.query.get(int(user_id))
    except (ValueError, TypeError):
        return None

# Initialize rate limiter for Socket.IO events
socket_rate_limiter = RateLimiter(max_requests=30, time_window=60)  # 30 requests per minute

# --- Utility Functions ---
def clear_user_session():
    """Clear all user-related session data"""
    session.pop('active_role', None)
    session.pop('operator_session_id', None)
    session.pop('operator_name', None)
    session.pop('machine_name', None)
    session.pop('shift', None)
    session.pop('current_operator_log_id', None)
    session.pop('current_drawing_id', None)
    session.pop('quality_inspector_name', None)

def get_machine_choices():
    """Get list of available machines for operator login"""
    try:
        machines = Machine.query.order_by(Machine.name).all()
        return [(m.name, m.name) for m in machines]
    except Exception as e:
        app.logger.error(f"Error getting machine choices: {str(e)}")
        return []  # Return empty list instead of hardcoded machines

# Configure logging
if not os.path.exists('logs'):
    os.makedirs('logs')

file_handler = RotatingFileHandler('logs/chipsight.log', maxBytes=1024 * 1024, backupCount=10)
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
))
file_handler.setLevel(logging.INFO)
app.logger.addHandler(file_handler)
app.logger.setLevel(logging.INFO)
app.logger.info('ChipSight startup')

# Configure backup directory
BACKUP_DIR = os.path.join(os.path.expanduser('~'), 'chipsight_backups')
if not os.path.exists(BACKUP_DIR):
    os.makedirs(BACKUP_DIR)

# Configure backup log file
BACKUP_LOG_FILE = os.path.join(BACKUP_DIR, 'backup_log.txt')
# ... up to line 106 (end of get_machine_choices) ...

def restore_operator_session(operator_name, machine_name):
    """
    Restore the most recent operator session state for a given operator and machine.
    Returns a dict with drawing_id, current_status, completed_quantity, planned_quantity if found, else None.
    """
    try:
        machine = Machine.query.filter_by(name=machine_name).first()
        if not machine:
            return None
        last_session = OperatorSession.query.filter_by(
            operator_name=operator_name,
            machine_id=machine.id
        ).order_by(OperatorSession.login_time.desc()).first()
        if last_session:
            last_log = OperatorLog.query.filter_by(
                operator_session_id=last_session.id
            ).order_by(OperatorLog.created_at.desc()).first()
            if last_log:
                return {
                    'drawing_id': last_log.drawing_id,
                    'current_status': last_log.current_status,
                    'completed_quantity': last_log.run_completed_quantity,
                    'planned_quantity': last_log.run_planned_quantity
                }
    except Exception as e:
        app.logger.error(f'Error restoring operator session: {str(e)}')
    return None

# ...line 108: def log_backup_action(action, details): ...

def log_backup_action(action, details):
    """Log backup-related actions to a dedicated log file"""
    try:
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        log_entry = f"[{timestamp}] {action}: {details}\n"
        
        with open(BACKUP_LOG_FILE, 'a') as f:
            f.write(log_entry)
    except Exception as e:
        app.logger.error(f'Failed to write to backup log: {str(e)}')

def backup_database():
    """Create a backup of the database file"""
    try:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_filename = f'digital_twin_{timestamp}.db'
        backup_path = os.path.join(BACKUP_DIR, backup_filename)
        
        # Only backup if the database file exists
        if os.path.exists('digital_twin.db'):
            shutil.copy2('digital_twin.db', backup_path)
            
            # Keep only last 7 days of backups
            for backup_file in os.listdir(BACKUP_DIR):
                backup_file_path = os.path.join(BACKUP_DIR, backup_file)
                if os.path.getctime(backup_file_path) < (datetime.now() - timedelta(days=7)).timestamp():
                    os.remove(backup_file_path)
            
            app.logger.info(f'Database backed up to {backup_path}')
            return backup_filename
        return None
    except Exception as e:
        app.logger.error(f'Database backup failed: {str(e)}')
        return None

def cleanup_old_data():
    """Clean up old data while preserving active projects and end products"""
    try:
        current_time = datetime.now(timezone.utc)
        
        # 1. Archive completed operator sessions older than 24 hours
        old_sessions = OperatorSession.query.filter(
            OperatorSession.logout_time < (current_time - timedelta(hours=24)),
            OperatorSession.is_active == False
        ).all()
        
        for session in old_sessions:
            # Archive session data to SystemLog before deletion
            log_entry = SystemLog(
                level='INFO',
                source='DataCleanup',
                message=f'Archived operator session for {session.operator_name} on machine {session.machine_rel.name}',
                resolved=True,
                resolved_by='System',
                resolved_at=current_time
            )
            db.session.add(log_entry)
            db.session.delete(session)
        
        # 2. Archive completed quality checks older than 24 hours
        old_checks = QualityCheck.query.filter(
            QualityCheck.timestamp < (current_time - timedelta(hours=24))
        ).all()
        
        for check in old_checks:
            # Archive quality check data to SystemLog before deletion
            log_entry = SystemLog(
                level='INFO',
                source='DataCleanup',
                message=f'Archived quality check for drawing {check.operator_log_rel.drawing_rel.drawing_number} by {check.inspector_name}',
                resolved=True,
                resolved_by='System',
                resolved_at=current_time
            )
            db.session.add(log_entry)
            db.session.delete(check)
        
        # 3. Archive completed operator logs older than 24 hours
        old_logs = OperatorLog.query.filter(
            OperatorLog.created_at < (current_time - timedelta(hours=24)),
            OperatorLog.current_status.in_(['cycle_completed', 'lpi_completed'])
        ).all()
        
        for log in old_logs:
            # Archive operator log data to SystemLog before deletion
            log_entry = SystemLog(
                level='INFO',
                source='DataCleanup',
                message=f'Archived operator log for drawing {log.drawing_rel.drawing_number} by {log.operator_session.operator_name}',
                resolved=True,
                resolved_by='System',
                resolved_at=current_time
            )
            db.session.add(log_entry)
            db.session.delete(log)
        
        # 4. Archive completed rework items older than 24 hours
        old_rework = ReworkQueue.query.filter(
            ReworkQueue.created_at < (current_time - timedelta(hours=24)),
            ReworkQueue.status.in_(['completed', 'scrapped'])
        ).all()
        
        for rework in old_rework:
            # Archive rework data to SystemLog before deletion
            log_entry = SystemLog(
                level='INFO',
                source='DataCleanup',
                message=f'Archived rework item for drawing {rework.drawing_rel.drawing_number}',
                resolved=True,
                resolved_by='System',
                resolved_at=current_time
            )
            db.session.add(log_entry)
            db.session.delete(rework)
        
        # 5. Archive completed machine breakdown logs older than 24 hours
        old_breakdowns = MachineBreakdownLog.query.filter(
            MachineBreakdownLog.breakdown_end_time < (current_time - timedelta(hours=24))
        ).all()
        
        for breakdown in old_breakdowns:
            # Archive breakdown data to SystemLog before deletion
            log_entry = SystemLog(
                level='INFO',
                source='DataCleanup',
                message=f'Archived machine breakdown for {breakdown.machine_rel.name}',
                resolved=True,
                resolved_by='System',
                resolved_at=current_time
            )
            db.session.add(log_entry)
            db.session.delete(breakdown)
        
        # Commit all changes
        db.session.commit()
        app.logger.info('Data cleanup completed successfully')
        return True
        
    except Exception as e:
        db.session.rollback()
        app.logger.error(f'Data cleanup failed: {str(e)}')
        return False

def reset_instance():
    """Reset the entire instance by deleting and recreating the database"""
    try:
        # Create a backup before reset
        backup_filename = backup_database()
        if backup_filename:
            app.logger.info(f"Created backup before reset: {backup_filename}")
        
        # Close all database connections
        db.session.close()
        db.engine.dispose()
        
        # Delete the database file
        db_path = 'digital_twin.db'
        if os.path.exists(db_path):
            try:
                # Try to delete the file
                os.remove(db_path)
                app.logger.info("Deleted existing database file")
            except PermissionError:
                # If permission error, try to force close any remaining connections
                import gc
                gc.collect()  # Force garbage collection
                db.session.remove()  # Remove any remaining sessions
                db.engine.dispose()  # Dispose engine again
                # Try to delete again
                os.remove(db_path)
                app.logger.info("Deleted existing database file after closing connections")
        
        # Create all tables
        db.create_all()
        app.logger.info("Created new database with tables")
        
        # Initialize machines one by one with error handling
        machines = [
            ('Leadwell-1', 'available', 'VMC'),
            ('Leadwell-2', 'available', 'VMC')
        ]
        for name, status, machine_type in machines:
            try:
                machine = Machine(name=name, status=status, machine_type=machine_type)
                db.session.add(machine)
                db.session.commit()
                app.logger.info(f"Added machine: {name}")
            except IntegrityError:
                db.session.rollback()
                app.logger.info(f"Machine {name} already exists")
        
        # Create admin user
        try:
            admin_user = User(
                username='admin',
                password=generate_password_hash('admin123'),
                role='admin',
                is_active=True
            )
            db.session.add(admin_user)
            db.session.commit()
            app.logger.info("Added admin user")
        except IntegrityError:
            db.session.rollback()
            app.logger.info("Admin user already exists")
        app.logger.info("Reset completed successfully")
        return True
    except Exception as e:
        app.logger.error(f"Error resetting instance: {str(e)}")
        db.session.rollback()
        return False

def ensure_machines_exist():
    try:
        existing_names = {m.name for m in Machine.query.all()}
        required_machines = [
            # VMC Machines
            ('Leadwell-1', 'VMC'), ('Leadwell-2', 'VMC'),
            ('AMS VMC-1', 'VMC'), ('AMS VMC-2', 'VMC'),
            ('AMS VMC-3', 'VMC'), ('AMS VMC-4', 'VMC'),
            ('HAAS-1', 'VMC'), ('HAAS-2', 'VMC'), ('IRUS', 'VMC'),
            
            # CNC Machines
            ('CNC1', 'CNC'), ('CNC2', 'CNC'), ('CNC3', 'CNC'), ('CNC4', 'CNC'),
            ('CNC5', 'CNC'), ('CNC6', 'CNC'), ('CNC7', 'CNC'), ('CNC8', 'CNC'), ('CNC9', 'CNC'),
            
            # Bandsaw Machines
            ('BANDSAW-1', 'BANDSAW'), ('BANDSAW-2', 'BANDSAW'),
            
            # Tapping Machines
            ('TAPPING MACHINE 1', 'TAPPING'), ('TAPPING MACHINE 2', 'TAPPING'), 
            ('TAPPING MACHINE 3', 'TAPPING'), ('TAPPING MACHINE 4', 'TAPPING'),
            
            # Welding Machines
            ('MIG WELDING', 'WELDING'), ('TIG WELDING', 'WELDING'), 
            ('LASER WELDING', 'WELDING'), ('ARC WELDING', 'WELDING'),
            
            # Milling Machines
            ('MILLING MACHINE 1', 'MILLING'),
            
            # Lathe Machines
            ('MANUAL LATHE 1', 'LATHE'),
            ('CNC LATHE 1', 'LATHE'), ('CNC LATHE 2', 'LATHE')
        ]
        for name, mtype in required_machines:
            if name not in existing_names:
                db.session.add(Machine(name=name, status='available', machine_type=mtype))
        db.session.commit()
        return True
    except Exception as e:
        app.logger.error(f"Error ensuring machines exist: {str(e)}")
        db.session.rollback()
        return False

def initialize_application():
    """Initialize the application with required setup"""
    try:
        app.logger.info("Starting application initialization...")
        
        # Initialize database
        db.create_all()
        app.logger.info("Database tables created")
        
        # Ensure required machines exist
        ensure_machines_exist()
        app.logger.info("Machine records verified")
        
        # Initialize scheduled tasks
        init_scheduled_tasks(app, socketio)
        app.logger.info("Scheduled tasks initialized")
        
        # Initialize live routes
        init_live_routes(app, socketio)
        app.logger.info("Live routes initialized")
        
        app.logger.info("Application initialization completed successfully")
        
    except Exception as e:
        app.logger.error(f"Error during application initialization: {str(e)}")
        raise

# Initialize the application at startup
with app.app_context():
    initialize_application()

@app.errorhandler(404)
def not_found_error(error):
    if request.path.startswith('/static/'):
        return "Static file not found.", 404
    log_error('NotFound', f'Page not found: {request.url}')
    return render_template('errors/404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    """Handle 500 errors"""
    try:
        db.session.rollback()
    except:
        pass
    finally:
        db.session.remove()
    
    stack_trace = traceback.format_exc()
    log_error('InternalServer', str(error), stack_trace)
    return render_template('errors/500.html'), 500

@app.errorhandler(SQLAlchemyError)
def database_error(error):
    """Handle database errors"""
    db.session.rollback()
    stack_trace = traceback.format_exc()
    log_error('Database', str(error), stack_trace)
    return render_template('errors/500.html'), 500

# Custom Jinja2 filter for nl2br
def nl2br_filter(value):
    """Convert newlines to <br> tags."""
    return Markup(str(value).replace('\n', '<br>\n'))

def escapejs_filter(value):
    """Escape strings for JavaScript."""
    value = str(value)
    for c in ['"', "'", '\n', '\r']:
        value = value.replace(c, '\\' + c)
    return value.replace('\\', '\\\\')

# Register filters
app.jinja_env.filters['nl2br'] = nl2br_filter
app.jinja_env.filters['escapejs'] = escapejs_filter

# Basic configuration
app.secret_key = os.environ.get('SECRET_KEY', 'your-secret-key-here')  # Change this to a secure random key
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.config['ALLOWED_EXTENSIONS'] = {'xlsx'}  # Only allow Excel files

# Session configuration (for cross-device login)
app.config['SESSION_COOKIE_DOMAIN'] = None
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = False

# Encoding configuration
app.config['JSON_AS_ASCII'] = False
app.jinja_env.charset = 'utf-8'
app.jinja_env.auto_reload = True
app.config['TEMPLATES_AUTO_RELOAD'] = True
# Configure Jinja2 template loader with explicit encoding
template_loader = FileSystemLoader(
    searchpath='templates',
    encoding='utf-8-sig'  # Handle UTF-8 with BOM
)
app.jinja_loader = template_loader

def allowed_file(filename, allowed_extensions=None):
    """Check if the file extension is allowed and size is within limits"""
    if allowed_extensions is None:
        allowed_extensions = app.config.get('ALLOWED_EXTENSIONS', set())
    
    # Check extension
    if not ('.' in filename and filename.rsplit('.', 1)[1].lower() in allowed_extensions):
        return False
    
    # Check file size if it's a request upload
    if request.files:
        file = request.files.get(next(iter(request.files)))
        if file:
            file.seek(0, 2)  # Seek to end
            size = file.tell()
            file.seek(0)  # Reset to beginning
            if size > app.config['MAX_CONTENT_LENGTH']:
                return False
    
    return True

# Initialize Flask-Migrate
from flask_migrate import Migrate
migrate = Migrate(app, db)

@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    if 'text/html' in response.headers.get('Content-Type', ''):
        response.headers['Content-Type'] = 'text/html; charset=utf-8'
    return response

# --- ROUTES ---

@app.route('/')
def home():
    """Redirect root URL to login page"""
    return redirect(url_for('login_general'))

# --- ADMIN ---
@app.route('/admin')
@login_required
def admin_dashboard():
    try:
        # Debug logging
        app.logger.info(f"Admin dashboard accessed by user: {current_user.id}, Role: {current_user.role}")
        app.logger.info(f"Session data: {dict(session)}")
        
        # Check if user is admin
        if not current_user.is_admin:
            app.logger.warning(f"Access denied for user {current_user.id} - not an admin")
            flash('Access denied. Admin privileges required.', 'danger')
            return redirect(url_for('login_general'))

        # Initialize database if needed
        from sqlalchemy import inspect
        inspector = inspect(db.engine)
        if not inspector.has_table('user'):
            app.logger.info("Initializing database and creating admin user")
            initialize_application()
            session['username'] = 'admin'
            session['active_role'] = 'admin'
            user = SimpleUser('admin', 'admin')
            login_user(user)
            app.logger.info("Admin user created and logged in")
            return redirect(url_for('admin_dashboard'))
        
        # Get system performance metrics
        import psutil
        import platform
        
        # CPU metrics
        cpu_percent = psutil.cpu_percent(interval=1)
        cpu_freq = psutil.cpu_freq()
        cpu_speed = f"{cpu_freq.current:.1f} MHz" if cpu_freq else "N/A"
        
        # Memory metrics
        memory = psutil.virtual_memory()
        memory_percent = memory.percent
        
        # Disk metrics
        disk = psutil.disk_usage('/')
        disk_percent = disk.percent
        
        # Get recent system logs
        recent_logs = SystemLog.query.order_by(SystemLog.timestamp.desc()).limit(10).all()
        
        # Get recent backups
        backups = []
        if os.path.exists(BACKUP_DIR):
            for file in os.listdir(BACKUP_DIR):
                if file.endswith('.db'):
                    file_path = os.path.join(BACKUP_DIR, file)
                    backups.append({
                        'filename': file,
                        'size': os.path.getsize(file_path),
                        'created': datetime.fromtimestamp(os.path.getctime(file_path))
                    })
            backups.sort(key=lambda x: x['created'], reverse=True)
        
        # Get database statistics
        total_users = User.query.count()
        active_users = User.query.filter_by(is_active=True).count()
        total_machines = Machine.query.count()
        active_machines = Machine.query.filter_by(status='in_use').count()
        total_projects = Project.query.filter_by(is_deleted=False).count()
        active_projects = Project.query.filter(
            Project.is_deleted == False,
            Project.end_products.any(EndProduct.completion_date >= datetime.now().date())
        ).count()
        total_drawings = MachineDrawing.query.count()
        total_quality_checks = QualityCheck.query.count()
        total_rework_items = ReworkQueue.query.count()
        
        # Prepare stats dictionary
        stats = {
            'active_sessions': active_users,
            'total_users': total_users,
            'active_projects': active_projects,
            'total_projects': total_projects,
            'active_machines': active_machines,
            'total_machines': total_machines,
            'total_drawings': total_drawings,
            'total_quality_checks': total_quality_checks,
            'total_rework_items': total_rework_items
        }
        
        # Prepare performance metrics
        performance_metrics = {
            'cpu_usage': f"{cpu_percent}%",
            'cpu_speed': cpu_speed,
            'memory_usage': f"{memory_percent}%",
            'disk_usage': f"{disk_percent}%",
            'database_size': f"{sum(os.path.getsize(os.path.join(BACKUP_DIR, f)) for f in os.listdir(BACKUP_DIR) if f.endswith('.db')) / (1024*1024):.2f} MB",
            'system_info': {
                'os': platform.system(),
                'os_version': platform.version(),
                'hostname': platform.node(),
                'processor': platform.processor() or 'Unknown'
            }
        }
        
        return render_template('admin.html',
                             stats=stats,
                             performance_metrics=performance_metrics,
                             error_logs=recent_logs,
                             backups=backups,
                             active_users=active_users,
                             machine_status={m.name: m.status for m in Machine.query.all()},
                             BACKUP_DIR=BACKUP_DIR)
                             
    except Exception as e:
        app.logger.error(f"Error in admin dashboard: {str(e)}")
        flash('An error occurred while loading the dashboard.', 'danger')
        return redirect(url_for('login_general'))

@app.route('/admin/export_logs', methods=['POST'])
def admin_export_logs():
    if session.get('active_role') != 'admin':
        flash('Access denied. Please login as Admin.', 'danger')
        return redirect(url_for('login_general'))

    logs = SystemLog.query.order_by(SystemLog.timestamp.desc()).all()
    
    # Create pandas DataFrame
    df = pd.DataFrame([{
        'Timestamp': log.timestamp,
        'Level': log.level,
        'Source': log.source,
        'Message': log.message,
        'Resolved': 'Yes' if log.resolved else 'No'
    } for log in logs])

    # Save to BytesIO
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, sheet_name='System Logs', index=False)
    
    output.seek(0)
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'system_logs_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
    )

@app.route('/admin/resolve_log', methods=['POST'])
def admin_resolve_log():
    if session.get('active_role') != 'admin':
        flash('Access denied. Please login as Admin.', 'danger')
        return redirect(url_for('login_general'))

    log_id = request.form.get('log_id')
    if log_id:
        log = SystemLog.query.get(log_id)
        if log:
            log.resolved = True
            log.resolved_by = session.get('username')
            log.resolved_at = datetime.now(timezone.utc)
            db.session.commit()
            flash('Log marked as resolved.', 'success')
        else:
            flash('Log not found.', 'danger')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/create_backup', methods=['POST'])
@login_required
def admin_create_backup():
    if not current_user.is_admin:
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('admin_dashboard'))
    
    try:
        backup_database()
        flash('Backup created successfully', 'success')
    except Exception as e:
        flash(f'Error creating backup: {str(e)}', 'danger')
    
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/list_backups')
@login_required
def admin_list_backups():
    if not current_user.is_admin:
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('admin_dashboard'))
    
    try:
        backups = []
        if os.path.exists(BACKUP_DIR):
            for filename in os.listdir(BACKUP_DIR):
                if filename.endswith('.db'):
                    file_path = os.path.join(BACKUP_DIR, filename)
                    file_stat = os.stat(file_path)
                    backups.append({
                        'filename': filename,
                        'size': file_stat.st_size,
                        'created': datetime.fromtimestamp(file_stat.st_ctime),
                        'modified': datetime.fromtimestamp(file_stat.st_mtime)
                    })
        
        # Sort backups by creation time, newest first
        backups.sort(key=lambda x: x['created'], reverse=True)
        return render_template('backup_list.html', backups=backups)
    except Exception as e:
        flash(f'Error listing backups: {str(e)}', 'danger')
        return redirect(url_for('admin_dashboard'))

@app.route('/admin/backup_logs')
@login_required
def admin_backup_logs():
    if not current_user.is_admin:
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('admin_dashboard'))
    
    try:
        logs = []
        log_file = os.path.join(BACKUP_DIR, 'backup_log.txt')
        if os.path.exists(log_file):
            with open(log_file, 'r') as f:
                logs = f.readlines()
        return render_template('backup_logs.html', logs=logs)
    except Exception as e:
        flash(f'Error reading backup logs: {str(e)}', 'danger')
        return redirect(url_for('admin_dashboard'))

@app.route('/admin/download_backup/<filename>')
@login_required
def admin_download_backup(filename):
    if not current_user.is_admin:
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('admin_dashboard'))
    
    try:
        file_path = os.path.join(BACKUP_DIR, filename)
        if os.path.exists(file_path):
            return send_file(
                file_path,
                as_attachment=True,
                download_name=filename
            )
        else:
            flash('Backup file not found', 'danger')
            return redirect(url_for('admin_list_backups'))
    except Exception as e:
        flash(f'Error downloading backup: {str(e)}', 'danger')
        return redirect(url_for('admin_list_backups'))

@app.route('/admin/reset_instance', methods=['POST'])
@login_required
def admin_reset_instance():
    """Handle system instance reset with proper error handling and recovery"""
    if not current_user.is_admin:
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('admin_dashboard'))
    
    # Verify admin password
    admin_password = request.form.get('admin_password')
    admin_user = User.query.filter_by(username='admin').first()
    if not admin_user or not check_password_hash(admin_user.password, admin_password):
        flash('Invalid admin password', 'danger')
        return redirect(url_for('admin_dashboard'))
    
    try:
        # Close all database connections
        db.session.close()
        db.engine.dispose()
        
        # Attempt to reset the instance
        if reset_instance():
            # Reinitialize the application
            with app.app_context():
                initialize_application()
            flash('Instance reset successfully. System has been reinitialized.', 'success')
            return redirect(url_for('login_general'))  # Redirect to login since session is cleared
        else:
            flash('Instance reset failed. Check logs for details.', 'danger')
            return redirect(url_for('admin_dashboard'))
            
    except Exception as e:
        error_msg = f'Error during instance reset: {str(e)}'
        app.logger.error(error_msg)
        flash(error_msg, 'danger')

        # Attempt recovery
        try:
            with app.app_context():
                db.create_all()
                initialize_application()
            flash('Recovery attempted after reset failure. Please verify system state.', 'warning')
        except Exception as recovery_error:
            app.logger.error(f'Recovery after reset failed: {str(recovery_error)}')
            flash('Recovery attempt failed. Manual intervention may be required.', 'danger')
        return redirect(url_for('admin_dashboard'))

def log_error(error_type, message, stack_trace=None):
    """Log error to database and file"""
    try:
        error_log = SystemLog(
            level='ERROR',
            source=error_type,
            message=message,
            stack_trace=stack_trace
        )
        db.session.add(error_log)
        db.session.commit()
        app.logger.error(f'{error_type}: {message}')
        if stack_trace:
            app.logger.error(f'Stack trace: {stack_trace}')
    except Exception as e:
        app.logger.error(f'Error logging failed: {str(e)}')

# --- AUTHENTICATION & ROLE MANAGEMENT ---
@app.route('/login', methods=['GET', 'POST'])
def login_general():
    if request.method == 'POST':
        password = request.form.get('password')
        role = request.form.get('role')
        
        app.logger.info(f"Login attempt - Role: {role}")
        
        # Initialize user as None
        user = None
        
        # Check credentials based on role
        if role == 'admin' and password == 'admin123':
            user = SimpleUser(f"{role}|{role}", role)
            app.logger.info("Admin login successful")
        elif role == 'plant_head' and password == 'plant123':
            user = SimpleUser(f"{role}|{role}", role)
            app.logger.info("Plant Head login successful")
        elif role == 'planner' and password == 'planner123':
            user = SimpleUser(f"{role}|{role}", role)
            app.logger.info("Planner login successful")
        elif role == 'manager' and password == 'manager123':
            user = SimpleUser(f"{role}|{role}", role)
            app.logger.info("Manager login successful")
        elif role == 'quality' and password == 'quality123':
            user = SimpleUser(f"{role}|{role}", role)
            app.logger.info("Quality login successful")
        
        if user:
            # Clear any existing session data
            clear_user_session()
            
            # Set session variables first
            session['username'] = role  # Use role as username since we don't have usernames anymore
            session['active_role'] = role
            
            # Then login the user
            login_user(user)
            app.logger.info(f"User logged in successfully as {role}")
            
            # Redirect based on role
            if role == 'admin':
                return redirect(url_for('admin_dashboard'))
            elif role == 'plant_head':
                return redirect(url_for('plant_head_dashboard'))
            elif role == 'planner':
                return redirect(url_for('planner_dashboard'))
            elif role == 'manager':
                return redirect(url_for('manager_dashboard'))
            elif role == 'quality':
                return redirect(url_for('quality_dashboard'))
        
        app.logger.warning(f"Login failed for role: {role}")
        flash('Invalid credentials. Please try again.', 'error')
        return redirect(url_for('login_general'))
    
    return render_template('login_general.html')

@app.route('/logout', methods=['GET', 'POST'])
def logout_general():
    """Handle user logout"""
    try:
        # Get current session data
        operator_session_id = session.get('operator_session_id')
        session.pop('quality_inspector_name', None)  # Clear quality inspector session
        
        # Close any active operator session
        if operator_session_id:
            with app.app_context():
                session = OperatorSession.query.get(operator_session_id)
                if session:
                    session.is_active = False
                    session.logout_time = datetime.now(timezone.utc)
                    db.session.commit()
        
        # Clear all session data
        session.clear()
        
        # Log out the user
        logout_user()
        
        flash('You have been logged out successfully.', 'success')
        return redirect(url_for('login_general'))
    except Exception as e:
        app.logger.error(f"Error during logout: {str(e)}")
        flash('An error occurred during logout.', 'danger')
        return redirect(url_for('login_general'))

@app.route('/plant_head_dashboard')
@login_required
def plant_head_dashboard():
    """Handle plant head dashboard view"""
    if not current_user.is_plant_head:
        flash('Access denied. Plant Head access required.', 'danger')
        return redirect(url_for('login_general'))
    
    try:
        # Get overall OEE
        average_oee = db.session.query(func.avg(OperatorLog.oee)).scalar() or 0
        average_oee = round(average_oee, 2)
        
        # Get machine utilization
        total_machines = Machine.query.count()
        active_machines = Machine.query.filter_by(status='running').count()
        machine_utilization = round((active_machines / total_machines * 100) if total_machines > 0 else 0, 2)
        
        # Get today's production count
        today = datetime.now(timezone.utc).date()
        todays_production = db.session.query(
            func.sum(OperatorLog.run_completed_quantity)
        ).filter(
            func.date(OperatorLog.created_at) == today,
            OperatorLog.quality_status == 'approved'
        ).scalar() or 0
        
        # Get machine names and status
        machines = Machine.query.all()
        machine_names = [m.name for m in machines]
        machine_running = [1 if m.status == 'running' else 0 for m in machines]
        machine_setup = [1 if m.status == 'setup' else 0 for m in machines]
        machine_stopped = [1 if m.status == 'stopped' else 0 for m in machines]
        
        # Get quality metrics
        quality_metrics = {
            'good_parts': db.session.query(func.sum(OperatorLog.run_completed_quantity)).filter(
                OperatorLog.quality_status == 'approved'
            ).scalar() or 0,
            'rejected': db.session.query(func.sum(OperatorLog.run_completed_quantity)).filter(
                OperatorLog.quality_status == 'rejected'
            ).scalar() or 0,
            'rework': db.session.query(func.sum(OperatorLog.run_completed_quantity)).filter(
                OperatorLog.quality_status == 'rework'
            ).scalar() or 0
        }
        
        # Get downtime analysis
        downtime = {
            'planned': db.session.query(func.count(MachineBreakdownLog.id)).filter(
                MachineBreakdownLog.breakdown_type == 'planned'
            ).scalar() or 0,
            'unplanned': db.session.query(func.count(MachineBreakdownLog.id)).filter(
                MachineBreakdownLog.breakdown_type == 'unplanned'
            ).scalar() or 0,
            'setup': db.session.query(func.count(MachineBreakdownLog.id)).filter(
                MachineBreakdownLog.breakdown_type == 'setup'
            ).scalar() or 0,
            'maintenance': db.session.query(func.count(MachineBreakdownLog.id)).filter(
                MachineBreakdownLog.breakdown_type == 'maintenance'
            ).scalar() or 0
        }
        
        # Get OEE trend data (last 30 days)
        timeline_dates = []
        oee_trend = []
        today = datetime.now(timezone.utc).date()
        
        for i in range(30):
            date = (today - timedelta(days=29-i))
            timeline_dates.append(date.strftime('%Y-%m-%d'))
            daily_oee = db.session.query(func.avg(OperatorLog.oee)).filter(
                func.date(OperatorLog.created_at) == date
            ).scalar() or 0
            oee_trend.append(round(daily_oee, 2))
        
        # Get production summary
        production_summary = []
        end_products = EndProduct.query.all()
        for ep in end_products:
            completed = db.session.query(func.sum(OperatorLog.run_completed_quantity)).filter(
                OperatorLog.end_product_sap_id == ep.sap_id,
                OperatorLog.quality_status == 'approved'
            ).scalar() or 0
            
            rejected = db.session.query(func.sum(OperatorLog.run_completed_quantity)).filter(
                OperatorLog.end_product_sap_id == ep.sap_id,
                OperatorLog.quality_status == 'rejected'
            ).scalar() or 0
            
            rework = db.session.query(func.sum(OperatorLog.run_completed_quantity)).filter(
                OperatorLog.end_product_sap_id == ep.sap_id,
                OperatorLog.quality_status == 'rework'
            ).scalar() or 0
            
            completion_percentage = round((completed / ep.quantity * 100) if ep.quantity > 0 else 0, 2)
            
            production_summary.append({
                'Product': ep.name,
                'SAP ID': ep.sap_id,
                'Planned Qty': ep.quantity,
                'Completed Qty': completed,
                'Rejected Qty': rejected,
                'Rework Qty': rework,
                'Completion %': completion_percentage
            })
        
        # Get critical issues (combining quality issues, breakdowns, and delays)
        critical_issues = []
        
        # Add quality issues
        quality_issues = db.session.query(OperatorLog).filter(
            OperatorLog.quality_status.in_(['rejected', 'rework']),
            OperatorLog.created_at >= (datetime.now(timezone.utc) - timedelta(days=1))
        ).order_by(OperatorLog.created_at.desc()).limit(5).all()
        
        for issue in quality_issues:
            critical_issues.append({
                'title': f'Quality Issue - {issue.quality_status.title()}',
                'description': f'SAP ID: {issue.end_product_sap_id}, Quantity: {issue.run_completed_quantity}',
                'time': issue.created_at.strftime('%Y-%m-%d %H:%M')
            })
        
        # Add recent breakdowns
        breakdowns = MachineBreakdownLog.query.filter(
            MachineBreakdownLog.created_at >= (datetime.now(timezone.utc) - timedelta(days=1))
        ).order_by(MachineBreakdownLog.created_at.desc()).limit(5).all()
        
        for breakdown in breakdowns:
            machine = Machine.query.get(breakdown.machine_id)
            critical_issues.append({
                'title': f'Machine Breakdown - {machine.name if machine else "Unknown"}',
                'description': breakdown.reason,
                'time': breakdown.created_at.strftime('%Y-%m-%d %H:%M')
            })
        
        # Sort critical issues by time
        critical_issues.sort(key=lambda x: datetime.strptime(x['time'], '%Y-%m-%d %H:%M'), reverse=True)
        
        return render_template('plant_head_dashboard.html',
            average_oee=average_oee,
            machine_utilization=machine_utilization,
            todays_production_count=todays_production,
            active_machines_count=active_machines,
            total_machines_count=total_machines,
            machine_names=machine_names,
            machine_running=machine_running,
            machine_setup=machine_setup,
            machine_stopped=machine_stopped,
            quality_metrics=quality_metrics,
            downtime=downtime,
            timeline_dates=timeline_dates,
            oee_trend=oee_trend,
            production_summary=production_summary,
            critical_issues=critical_issues
        )
        
    except Exception as e:
        app.logger.error(f"Error in plant head dashboard: {str(e)}")
        flash('An error occurred while loading the dashboard.', 'danger')
        return redirect(url_for('login_general'))

@app.route('/machine_shop')
@login_required
def machine_shop():
    """Handle machine shop view"""
    if not (current_user.is_plant_head or current_user.is_manager):
        flash('Access denied. Plant Head or Manager access required.', 'danger')
        return redirect(url_for('login_general'))
    try:
        # Flat list of all machines
        all_machines = Machine.query.all()
        # Show ALL assignments, regardless of status
        assignments = db.session.query(
            MachineDrawingAssignment
        ).options(
            joinedload(MachineDrawingAssignment.machine_rel),
            joinedload(MachineDrawingAssignment.drawing_rel).joinedload(MachineDrawing.end_product_rel)
        ).all()
        print('DEBUG: Assignments for machine shop page:')
        for a in assignments:
            print(f"ID={a.id}, Drawing={a.drawing_id}, Machine={a.machine_id}, Qty={a.assigned_quantity}, Status={a.status}, MachineName={a.machine_rel.name if a.machine_rel else None}, DrawingNum={a.drawing_rel.drawing_number if a.drawing_rel else None}")
        for assignment in assignments:
            if assignment.drawing_rel and assignment.drawing_rel.id:
                total_assigned = db.session.query(func.sum(MachineDrawingAssignment.assigned_quantity)).filter(
                    MachineDrawingAssignment.drawing_id == assignment.drawing_rel.id
                ).scalar() or 0
                assignment.total_assigned = total_assigned
            else:
                assignment.total_assigned = None
        # Route data for Vue (if needed)
        routes = ProcessRoute.query.filter(
            ProcessRoute.status.in_(['PENDING', 'IN_PROGRESS'])
        ).all()
        routes_data = []
        for route in routes:
            route_data = {
                'id': route.id,
                'sap_id': route.sap_id,
                'status': route.status,
                'total_quantity': route.total_quantity,
                'deadline': route.deadline.isoformat() if route.deadline else None,
                'operations': []
            }
            operations = RouteOperation.query.filter_by(route_id=route.id).order_by(RouteOperation.sequence).all()
            for op in operations:
                op_data = {
                    'id': op.id,
                    'sequence': op.sequence,
                    'operation_type': op.operation_type,
                    'assigned_machine': op.assigned_machine,
                    'status': op.status
                }
                route_data['operations'].append(op_data)
            routes_data.append(route_data)
        return render_template(
            'machine_shop.html',
            machines=all_machines,
            assignments=assignments,
            total_assignments=len(assignments),
            running_jobs=sum(1 for a in assignments if a.status == 'running'),
            pending_assignments=sum(1 for a in assignments if a.status == 'assigned'),
            completed_today=sum(1 for a in assignments if a.status == 'completed' and a.completed_at and a.completed_at.date() == datetime.now().date()),
            routes=routes_data
        )
    except Exception as e:
        app.logger.error(f'Error in machine shop view: {str(e)}')
        flash('An error occurred while loading the machine shop view.', 'danger')
        return redirect(url_for('login_general'))

def get_current_job(machine_id):
    """Get the current job details for a machine"""
    try:
        current_job = OperatorLog.query.filter_by(
            machine_id=machine_id,
            status='IN_PROGRESS'
        ).first()
        
        if current_job and current_job.drawing:
            return {
                'sap_id': current_job.drawing.sap_id,
                'completed': current_job.run_completed_quantity,
                'total': current_job.run_total_quantity
            }
    except Exception as e:
        app.logger.error(f'Error getting current job for machine {machine_id}: {str(e)}')
    return None

def get_completed_today():
    """Get count of assignments completed today"""
    try:
        today = datetime.now().date()
        return OperatorLog.query.filter(
            func.date(OperatorLog.end_time) == today,
            OperatorLog.status == 'COMPLETED'
        ).count()
    except Exception as e:
        app.logger.error(f'Error getting completed jobs count: {str(e)}')
        return 0

@app.route('/machine_shop/assign', methods=['POST'])
@login_required
def machine_shop_assign():
    """Handle machine drawing assignment"""
    if not (current_user.is_plant_head or current_user.is_manager):
        flash('Access denied. Plant Head or Manager access required.', 'danger')
        return redirect(url_for('machine_shop'))
    try:
        drawing_number = request.form.get('drawing_number')
        machine_id = request.form.get('machine_id')
        quantity = int(request.form.get('quantity'))
        tool_number = request.form.get('tool_number', '')
        # Tool validation (optional, but if present must be valid)
        if tool_number:
            tools = tool_number.split(',')
            import re
            valid = all(tool and re.match(r'^[A-Z0-9\-_.#@/]+$', tool) for tool in tools)
            if not valid:
                flash('Invalid tool format: Use only uppercase, numbers, and -_.#@/ (no spaces, no empty names)', 'danger')
                return redirect(url_for('machine_shop'))
        # Validate other inputs
        if not all([drawing_number, machine_id, quantity]):
            flash('Drawing number, machine, and quantity are required.', 'danger')
            return redirect(url_for('machine_shop'))
        # Get drawing and machine
        # --- PATCH START: Ensure EndProduct exists for SAP ID ---
        from models import Project, EndProduct
        sap_id = drawing_number.strip()
        end_product = EndProduct.query.filter_by(sap_id=sap_id).first()
        if not end_product:
            # Try to find or create a placeholder project
            project = Project.query.filter_by(project_code='UNASSIGNED').first()
            if not project:
                project = Project(project_code='UNASSIGNED', project_name='Unassigned Project')
                db.session.add(project)
                db.session.flush()  # get project.id
            # Create placeholder EndProduct
            end_product = EndProduct(
                project_id=project.id,
                sap_id=sap_id,
                name=f'Product {sap_id}',
                quantity=1,
                completion_date=datetime.now(timezone.utc).date(),
                setup_time_std=1.0,
                cycle_time_std=1.0,
                is_first_piece_fpi_required=True,
                is_last_piece_lpi_required=True,
                created_at=datetime.now(timezone.utc)
            )
            db.session.add(end_product)
            db.session.flush()

        from models import ProcessRoute
        process_route = ProcessRoute.query.filter_by(sap_id=sap_id).first()
        if not process_route:
            process_route = ProcessRoute(
                sap_id=sap_id,
                total_quantity=quantity,
                created_by=getattr(current_user, 'role', 'system'),
                created_at=datetime.now(timezone.utc),
                status='PENDING'
            )
            db.session.add(process_route)
            db.session.flush()
              # get end_product in session
        # --- PATCH END ---
        drawing = MachineDrawing.query.filter(
            db.or_(func.trim(MachineDrawing.drawing_number) == drawing_number.strip(), func.trim(MachineDrawing.sap_id) == drawing_number.strip())
        ).first()
        if not drawing:
            # Auto-create MachineDrawing if not found
            drawing = MachineDrawing(
                drawing_number=drawing_number.strip(),
                sap_id=drawing_number.strip()
            )
            db.session.add(drawing)
            db.session.commit()
        machine = Machine.query.get(machine_id)
        if not drawing or not machine:
            flash('Invalid drawing number or machine.', 'danger')
            return redirect(url_for('machine_shop'))
        # Create assignment
        assignment = MachineDrawingAssignment(
            drawing_id=drawing.id,
            machine_id=machine.id,
            assigned_quantity=quantity,
            tool_number=tool_number,
            status='assigned',
            assigned_by=current_user.id,
            assigned_at=datetime.now(timezone.utc)
        )
        db.session.add(assignment)
        db.session.commit()
        # Emit real-time update for part finder
        from flask_socketio import SocketIO
        try:
            socketio.emit('part_finder_update', {
                'sap_id': drawing.sap_id,
                'drawing_number': drawing.drawing_number
            })
        except Exception as emit_err:
            print(f"SocketIO emit error: {emit_err}")
        # Debug: print all assignments after creation
        all_assignments = MachineDrawingAssignment.query.all()
        print('DEBUG: All assignments after creation:')
        for a in all_assignments:
            print(f"ID={a.id}, Drawing={a.drawing_id}, Machine={a.machine_id}, Qty={a.assigned_quantity}, Status={a.status}, MachineName={a.machine_rel.name if a.machine_rel else None}, DrawingNum={a.drawing_rel.drawing_number if a.drawing_rel else None}")
        flash('Assignment created successfully.', 'success')
        return redirect(url_for('machine_shop'))
    except Exception as e:
        db.session.rollback()
        flash(f'Error creating assignment: {str(e)}', 'danger')
        return redirect(url_for('machine_shop'))

@app.route('/machine_shop/transfer', methods=['POST'])
@login_required
def machine_shop_transfer():
    """Handle machine drawing transfer"""
    if not (current_user.is_plant_head or current_user.is_manager):
        flash('Access denied. Plant Head or Manager access required.', 'danger')
        return redirect(url_for('machine_shop'))
    
    try:
        assignment_id = request.form.get('assignment_id')
        to_machine_id = request.form.get('to_machine_id')
        quantity = int(request.form.get('quantity'))
        reason = request.form.get('reason')
        
        # Validate inputs
        if not all([assignment_id, to_machine_id, quantity, reason]):
            flash('All fields are required.', 'danger')
            return redirect(url_for('machine_shop'))
        
        # Get assignment and target machine
        assignment = MachineDrawingAssignment.query.get(assignment_id)
        to_machine = Machine.query.get(to_machine_id)
        
        if not assignment or not to_machine:
            flash('Invalid assignment or target machine.', 'danger')
            return redirect(url_for('machine_shop'))
        
        # Create transfer history
        transfer = TransferHistory(
            assignment_id=assignment.id,
            from_machine_id=assignment.machine_id,
            to_machine_id=to_machine.id,
            quantity_transferred=quantity,  # fixed field name
            reason=reason,
            transferred_by=current_user.id,
            transferred_at=datetime.now(timezone.utc)
        )
        
        # Update assignment
        assignment.machine_id = to_machine.id
        assignment.assigned_quantity = quantity
        
        # --- PATCH: Partial transfer logic ---
        if quantity < assignment.assigned_quantity:
            # Reduce old assignment
            assignment.assigned_quantity -= quantity
            # Create new assignment for new machine
            new_assignment = MachineDrawingAssignment(
                drawing_id=assignment.drawing_id,
                machine_id=to_machine.id,
                assigned_quantity=quantity,
                tool_number=assignment.tool_number,
                status='assigned',
                assigned_by=current_user.id,
                assigned_at=datetime.now(timezone.utc)
            )
            db.session.add(new_assignment)
        else:
            # All quantity moved, update assignment to new machine
            assignment.machine_id = to_machine.id
            assignment.status = 'transferred'
            assignment.assigned_quantity = quantity
        # --- END PATCH ---
        db.session.add(transfer)
        db.session.commit()
        # In-app notification for transfer
        socketio.emit('notify_transfer', {'drawing': assignment.drawing_rel.drawing_number, 'from': assignment.machine_rel.name, 'to': to_machine.name, 'quantity': quantity})
        
        flash(f'Drawing transferred to {to_machine.name} successfully.', 'success')
        # Emit real-time update
        assignments = db.session.query(MachineDrawingAssignment).options(
            joinedload(MachineDrawingAssignment.machine_rel),
            joinedload(MachineDrawingAssignment.drawing_rel).joinedload(MachineDrawing.end_product_rel)
        ).all()
        assignments_data = []
        for a in assignments:
            assignments_data.append({
                'machine_name': a.machine_rel.name if a.machine_rel else '',
                'drawing_number': a.drawing_rel.drawing_number if a.drawing_rel else '',
                'tool_number': a.tool_number,
                'assigned_quantity': a.assigned_quantity,
                'completed_quantity': a.completed_quantity,
                'total_quantity': a.drawing_rel.end_product_rel.quantity if a.drawing_rel and a.drawing_rel.end_product_rel else '',
                'remaining_quantity': (a.drawing_rel.end_product_rel.quantity - a.total_assigned) if a.drawing_rel and a.drawing_rel.end_product_rel and a.total_assigned is not None else '',
                'status': a.status,
                'status_class': 'success' if a.status == 'running' else 'warning' if a.status == 'assigned' else 'info' if a.status == 'completed' else 'secondary',
                'current_operator': getattr(a, 'current_operator', '')
            })
        socketio.emit('assignments_update', {'assignments': assignments_data})
        return redirect(url_for('machine_shop'))
        
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error in machine shop transfer: {str(e)}")
        flash('An error occurred while transferring the drawing.', 'danger')
        return redirect(url_for('machine_shop'))

@app.route('/machine_shop/history/<int:assignment_id>')
@login_required
def machine_shop_history(assignment_id):
    """Get history for a machine drawing assignment"""
    if not (current_user.is_plant_head or current_user.is_manager):
        return jsonify({'error': 'Access denied'}), 403
    
    try:
        # Get transfer history
        transfers = TransferHistory.query.filter_by(assignment_id=assignment_id).order_by(TransferHistory.transferred_at.desc()).all()
        
        history = []
        for transfer in transfers:
            from_machine = Machine.query.get(transfer.from_machine_id)
            to_machine = Machine.query.get(transfer.to_machine_id)
            history.append({
                'timestamp': transfer.transferred_at.strftime('%Y-%m-%d %H:%M:%S'),
                'action': 'Transfer',
                'from': from_machine.name if from_machine else 'Unknown',
                'to': to_machine.name if to_machine else 'Unknown',
                'quantity': transfer.quantity,
                'by': transfer.transferred_by,
                'reason': transfer.reason
            })
        
        return jsonify({'history': history})
        
    except Exception as e:
        app.logger.error(f"Error getting machine shop history: {str(e)}")
        return jsonify({'error': 'An error occurred'}), 500

@app.route('/api/drawing_info/<drawing_number>')
@login_required
def get_drawing_info(drawing_number):
    """Get information about a drawing for auto-fill"""
    if not (current_user.is_plant_head or current_user.is_manager):
        return jsonify({'error': 'Access denied'}), 403
    
    try:
        # Try to find drawing by drawing number or SAP ID
        drawing = MachineDrawing.query.filter(
            db.or_(
                func.trim(MachineDrawing.drawing_number) == drawing_number.strip(),
                func.trim(MachineDrawing.sap_id) == drawing_number.strip()
            )
        ).first()
        
        if not drawing:
            return jsonify({'error': 'Drawing not found'}), 404
        
        # Get total quantity from end product
        total_quantity = drawing.end_product_rel.quantity if drawing.end_product_rel else 0
        
        # Get assigned quantity
        assigned_quantity = db.session.query(
            func.sum(MachineDrawingAssignment.assigned_quantity)
        ).filter(
            MachineDrawingAssignment.drawing_id == drawing.id
        ).scalar() or 0
        
        # Get most recently used tool
        recent_assignment = MachineDrawingAssignment.query.filter_by(
            drawing_id=drawing.id
        ).order_by(MachineDrawingAssignment.assigned_at.desc()).first()
        
        return jsonify({
            'total_quantity': total_quantity,
            'assigned_quantity': assigned_quantity,
            'remaining_quantity': total_quantity - assigned_quantity,
            'recent_tool': recent_assignment.tool_number if recent_assignment else ''
        })
        
    except Exception as e:
        app.logger.error(f"Error getting drawing info: {str(e)}")
        return jsonify({'error': 'An error occurred'}), 500

@app.route('/live_dpr')
@login_required
def live_dpr():
    """Handle live DPR view"""
    if not (current_user.is_plant_head or current_user.is_manager or current_user.is_planner):
        flash('Access denied. Plant Head, Manager, or Planner access required.', 'danger')
        return redirect(url_for('login_general'))
    
    try:
        # Get all machines for filtering
        machines = Machine.query.all()
        
        # Get initial DPR data
        production_data = get_live_production_data(db)
        
        # Get current time in UTC
        now = datetime.now(timezone.utc)
        
        # Get operator logs for today (in UTC)
        today = now.date()
        logs = db.session.query(OperatorLog).filter(
            func.date(OperatorLog.created_at) == today
        ).order_by(OperatorLog.created_at.desc()).all()
        
        # Format logs for display
        formatted_logs = []
        for log in logs:
            # Get related data
            machine = Machine.query.get(log.machine_id) if log.machine_id else None
            drawing = log.drawing_rel
            end_product = drawing.end_product_rel if drawing else None
            project = end_product.project_rel if end_product else None
            
            # Calculate times
            setup_time = (log.setup_end_time - log.setup_start_time).total_seconds() / 60 if log.setup_end_time and log.setup_start_time else None
            cycle_time = (log.last_cycle_end_time - log.first_cycle_start_time).total_seconds() / 60 if log.last_cycle_end_time and log.first_cycle_start_time else None
            
            # Ensure all timestamps are timezone-aware
            setup_start = ensure_timezone_aware(log.setup_start_time) if log.setup_start_time else None
            setup_end = ensure_timezone_aware(log.setup_end_time) if log.setup_end_time else None
            cycle_start = ensure_timezone_aware(log.first_cycle_start_time) if log.first_cycle_start_time else None
            cycle_end = ensure_timezone_aware(log.last_cycle_end_time) if log.last_cycle_end_time else None
            
            # Find the assignment for this machine and drawing
            assignment = None
            if log.machine_id and log.drawing_id:
                assignment = db.session.query(MachineDrawingAssignment).filter_by(
                    machine_id=log.machine_id,
                    drawing_id=log.drawing_id
                ).order_by(MachineDrawingAssignment.assigned_at.desc()).first()
            # Use assignment_id if present, else fallback to old lookup
            if getattr(log, 'assignment_id', None):
                tool_number = log.assignment.tool_number if log.assignment else ''
            else:
                assignment = None
                if log.machine_id and log.drawing_id:
                    assignment = db.session.query(MachineDrawingAssignment).filter_by(
                        machine_id=log.machine_id,
                        drawing_id=log.drawing_id
                    ).order_by(MachineDrawingAssignment.assigned_at.desc()).first()
                tool_number = assignment.tool_number if assignment else ''
            formatted_logs.append({
                'date': log.created_at.strftime('%Y-%m-%d'),
                'mc_do': machine.name if machine else 'Unknown',
                'operator': log.operator_session.operator_name if log.operator_session else 'Unknown',
                'proj': project.project_code if project else 'Unknown',
                'sap_no': end_product.sap_id if end_product else 'Unknown',
                'drg_no': drawing.drawing_number if drawing else 'Unknown',
                'setup_start_utc': setup_start.strftime('%H:%M:%S') if setup_start else '',
                'setup_end_utc': setup_end.strftime('%H:%M:%S') if setup_end else '',
                'first_cycle_start_utc': cycle_start.strftime('%H:%M:%S') if cycle_start else '',
                'last_cycle_end_utc': cycle_end.strftime('%H:%M:%S') if cycle_end else '',
                'planned': log.run_planned_quantity,
                'completed': log.run_completed_quantity,
                'passed': log.run_completed_quantity - (log.run_rejected_quantity_fpi + log.run_rejected_quantity_lpi + log.run_rework_quantity_fpi + log.run_rework_quantity_lpi),
                'rejected': log.run_rejected_quantity_fpi + log.run_rejected_quantity_lpi,
                'rework': log.run_rework_quantity_fpi + log.run_rework_quantity_lpi,
                'std_setup_time': log.standard_setup_time,
                'actual_setup_time': setup_time,
                'std_cycle_time': log.standard_cycle_time,
                'actual_cycle_time': cycle_time,
                'total_cycle_time': cycle_time * log.run_completed_quantity if cycle_time else None,
                'fpi_status': log.fpi_status,
                'lpi_status': log.lpi_status,
                'status': log.current_status,
                'availability': log.availability,
                'performance': log.performance,
                'quality': log.quality_rate,
                'oee': log.oee,
                'tool_number': tool_number,
            })
        
        return render_template('live_dpr.html',
            machines=machines,
            logs=formatted_logs,
            dpr_data=formatted_logs,  # Pass detailed logs for JS filtering
            now=now
        )
        
    except Exception as e:
        app.logger.error(f"Error in live DPR view: {str(e)}")
        flash('An error occurred while loading the live DPR view.', 'danger')
        return redirect(url_for('login_general'))

@app.route('/live_dpr/download')
@login_required
def live_dpr_download():
    """Download current DPR data as CSV"""
    if not (current_user.is_plant_head or current_user.is_manager or current_user.is_planner):
        flash('Access denied. Plant Head, Manager, or Planner access required.', 'danger')
        return redirect(url_for('login_general'))
    try:
        production_data = get_live_production_data(db)
        now = datetime.now(timezone.utc)
        today = now.date()
        logs = db.session.query(OperatorLog).filter(
            func.date(OperatorLog.created_at) == today
        ).order_by(OperatorLog.created_at.desc()).all()
        # Format logs for CSV
        import csv
        from io import StringIO
        si = StringIO()
        writer = csv.writer(si)
        writer.writerow(['Date', 'Machine', 'Operator', 'Project', 'SAP No', 'Drawing No', 'Tool', 'Setup Start', 'Setup End', 'First Cycle Start', 'Last Cycle End', 'Planned', 'Completed', 'Passed', 'Rejected', 'Rework', 'Std Setup Time', 'Actual Setup Time', 'Std Cycle Time', 'Actual Cycle Time', 'Total Cycle Time', 'FPI Status', 'LPI Status', 'Status', 'Availability', 'Performance', 'Quality', 'OEE'])
        for log in logs:
            machine = Machine.query.get(log.machine_id) if log.machine_id else None
            drawing = log.drawing_rel
            end_product = drawing.end_product_rel if drawing else None
            project = end_product.project_rel if end_product else None
            setup_time = (log.setup_end_time - log.setup_start_time).total_seconds() / 60 if log.setup_end_time and log.setup_start_time else None
            cycle_time = (log.last_cycle_end_time - log.first_cycle_start_time).total_seconds() / 60 if log.last_cycle_end_time and log.first_cycle_start_time else None
            setup_start = ensure_timezone_aware(log.setup_start_time) if log.setup_start_time else None
            setup_end = ensure_timezone_aware(log.setup_end_time) if log.setup_end_time else None
            cycle_start = ensure_timezone_aware(log.first_cycle_start_time) if log.first_cycle_start_time else None
            cycle_end = ensure_timezone_aware(log.last_cycle_end_time) if log.last_cycle_end_time else None
            assignment = None
            if log.machine_id and log.drawing_id:
                assignment = db.session.query(MachineDrawingAssignment).filter_by(
                    machine_id=log.machine_id,
                    drawing_id=log.drawing_id
                ).order_by(MachineDrawingAssignment.assigned_at.desc()).first()
            # Use assignment_id if present, else fallback to old lookup
            if getattr(log, 'assignment_id', None):
                tool_number = log.assignment.tool_number if log.assignment else ''
            else:
                assignment = None
                if log.machine_id and log.drawing_id:
                    assignment = db.session.query(MachineDrawingAssignment).filter_by(
                        machine_id=log.machine_id,
                        drawing_id=log.drawing_id
                    ).order_by(MachineDrawingAssignment.assigned_at.desc()).first()
                tool_number = assignment.tool_number if assignment else ''
            writer.writerow([
                log.created_at.strftime('%Y-%m-%d'),
                machine.name if machine else 'Unknown',
                log.operator_session.operator_name if log.operator_session else 'Unknown',
                project.project_code if project else 'Unknown',
                end_product.sap_id if end_product else 'Unknown',
                drawing.drawing_number if drawing else 'Unknown',
                tool_number,
                setup_start.strftime('%H:%M:%S') if setup_start else '',
                setup_end.strftime('%H:%M:%S') if setup_end else '',
                cycle_start.strftime('%H:%M:%S') if cycle_start else '',
                cycle_end.strftime('%H:%M:%S') if cycle_end else '',
                log.run_planned_quantity,
                log.run_completed_quantity,
                log.run_completed_quantity - (log.run_rejected_quantity_fpi + log.run_rejected_quantity_lpi + log.run_rework_quantity_fpi + log.run_rework_quantity_lpi),
                log.run_rejected_quantity_fpi + log.run_rejected_quantity_lpi,
                log.run_rework_quantity_fpi + log.run_rework_quantity_lpi,
                log.standard_setup_time,
                setup_time,
                log.standard_cycle_time,
                cycle_time,
                cycle_time * log.run_completed_quantity if cycle_time else None,
                log.fpi_status,
                log.lpi_status,
                log.current_status,
                log.availability,
                log.performance,
                log.quality_rate,
                log.oee
            ])
        output = si.getvalue()
        return Response(
            output,
            mimetype='text/csv',
            headers={
                'Content-Disposition': f'attachment;filename=live_dpr_{now.strftime("%Y%m%d")}.csv'
            }
        )
    except Exception as e:
        app.logger.error(f"Error in live DPR download: {str(e)}")
        flash('An error occurred while downloading the DPR data.', 'danger')
        return redirect(url_for('live_dpr'))

# --- PLANNER ---

@app.route('/planner_dashboard', methods=['GET', 'POST'])
@login_required
def planner_dashboard():
    """Handle planner dashboard"""
    if not current_user.is_planner:
        flash('Access denied. Planner access required.', 'danger')
        return redirect(url_for('login_general'))
        
    # Get end product data for each project
    projects_data = []
    for project in Project.query.filter_by(is_deleted=False).all():
        end_products = []
        for ep in EndProduct.query.filter_by(project_id=project.id).all():
            completed = db.session.query(
                func.sum(OperatorLog.run_completed_quantity)
            ).filter(
                OperatorLog.end_product_sap_id == ep.sap_id,
                OperatorLog.quality_status == 'approved'
            ).scalar() or 0
            
            end_products.append({
                'name': ep.name,
                'sap_id': ep.sap_id,
                'quantity': ep.quantity,
                'completed': completed
            })
        
        projects_data.append({
            'project_name': project.project_name,
            'end_products': end_products
        })

    try:
        if request.method == 'POST':
            action = request.form.get('action')
            
            if action == 'delete_project':
                project_id = request.form.get('project_id')
                if project_id:
                    project = Project.query.get(project_id)
                    if project:
                        project.is_deleted = True
                        project.deleted_at = datetime.now(timezone.utc)
                        db.session.commit()
                        flash('Project deleted successfully.', 'success')
                    else:
                        flash('Project not found.', 'danger')
                return redirect(url_for('planner_dashboard'))
                
            elif action == 'delete_end_product':
                end_product_id = request.form.get('end_product_id')
                if end_product_id:
                    end_product = EndProduct.query.get(end_product_id)
                    if end_product:
                        db.session.delete(end_product)
                        db.session.commit()
                        flash('End product deleted successfully.', 'success')
                    else:
                        flash('End product not found.', 'danger')
                return redirect(url_for('planner_dashboard'))
                
            elif 'production_plan_file' in request.files:
                file = request.files['production_plan_file']
                if file and allowed_file(file.filename):
                    try:
                        # Read the Excel file
                        df = pd.read_excel(file, dtype={'project_code': str})
                        
                        # Validate required columns
                        required_columns = ['project_code', 'project_name', 'sap_id', 'end_product', 'qty', 'st', 'ct', 'completion_date']
                        missing_columns = [col for col in required_columns if col not in df.columns]
                        if missing_columns:
                            flash(f'Missing required columns: {", ".join(missing_columns)}', 'danger')
                            return redirect(url_for('planner_dashboard'))

                        # Validate types
                        for col in ['qty', 'st', 'ct']:
                            if not pd.api.types.is_numeric_dtype(df[col]):
                                flash(f'Column {col} must be numeric.', 'danger')
                                return redirect(url_for('planner_dashboard'))
                        
                        if not pd.api.types.is_datetime64_any_dtype(df['completion_date']):
                            try:
                                df['completion_date'] = pd.to_datetime(df['completion_date'])
                            except Exception:
                                flash('completion_date column must be a valid date.', 'danger')
                                return redirect(url_for('planner_dashboard'))
                        
                        # Process each row
                        for _, row in df.iterrows():
                            # Create or update project
                            project = Project.query.filter_by(project_code=row['project_code']).first()
                            if not project:
                                project = Project(
                                    project_code=row['project_code'],
                                    project_name=row['project_name'],
                                    is_deleted=False
                                )
                                db.session.add(project)
                                db.session.flush()  # Get project ID for end product

                            # Create or update end product
                            end_product = EndProduct.query.filter_by(sap_id=row['sap_id']).first()
                            if not end_product:
                                end_product = EndProduct(
                                    project_id=project.id,
                                    sap_id=row['sap_id'],
                                    name=row['end_product'],
                                    quantity=row['qty'],
                                    setup_time_std=row['st'],
                                    cycle_time_std=row['ct'],
                                    completion_date=pd.to_datetime(row['completion_date']).date(),
                                    created_at=datetime.now(timezone.utc)  # Add created_at for new products
                                )
                                db.session.add(end_product)
                            else:
                                # Update existing end product
                                end_product.project_id = project.id 
                                end_product.name = row['end_product']
                                end_product.quantity = row['qty']
                                end_product.setup_time_std = row['st']
                                end_product.cycle_time_std = row['ct']
                                end_product.completion_date = pd.to_datetime(row['completion_date']).date()

                            # --- NEW: Parse route column and create ProcessRoute/RouteOperation ---
                            route_str = row.get('route') if 'route' in row else None
                            if route_str:
                                # Create or update ProcessRoute
                                process_route = ProcessRoute.query.filter_by(sap_id=row['sap_id']).first()
                                if not process_route:
                                    process_route = ProcessRoute(
                                        sap_id=row['sap_id'],
                                        total_quantity=row['qty'],
                                        created_by=current_user.role,
                                        created_at=datetime.now(timezone.utc),
                                        status='PENDING'
                                    )
                                    db.session.add(process_route)
                                    db.session.flush()
                                else:
                                    process_route.total_quantity = row['qty']
                                    process_route.status = 'PENDING'
                                # Remove old operations
                                RouteOperation.query.filter_by(route_id=process_route.id).delete()
                                # Split route string and create operations
                                steps = [s.strip().upper().replace(' ', '_') for s in route_str.replace('-', ',').split(',') if s.strip()]
                                for idx, step in enumerate(steps):
                                    op = RouteOperation(
                                        route_id=process_route.id,
                                        operation_type=step,
                                        sequence=idx+1,
                                        status='NOT_STARTED'
                                    )
                                    db.session.add(op)
                        # --- END NEW ---
                        db.session.commit()
                        flash('Production plan uploaded and processed successfully!', 'success')
                    except Exception as e:
                        db.session.rollback()
                        app.logger.error(f'Error processing production plan: {str(e)}')
                        flash(f'Error uploading plan: {str(e)}', 'danger')
                else:
                    flash('Invalid file type or no file selected. Please upload an .xlsx file.', 'warning')
                return redirect(url_for('planner_dashboard'))

        # Get active projects count
        active_projects = Project.query.filter_by(is_deleted=False).count()
        
        # Get total drawings
        total_drawings = MachineDrawing.query.count()

        # Calculate project progress and completion
        today = datetime.now(timezone.utc).date()
        projects = Project.query.filter_by(is_deleted=False).all()
        on_time_projects = 0
        delayed_projects = 0
        project_names = []
        project_completion = []
        project_colors = []
        project_border_colors = []
        
        # Calculate completion for each project
        for project in projects:
            # Get all end products for this project
            end_products = EndProduct.query.filter_by(project_id=project.id).all()
            total_quantity = sum(ep.quantity for ep in end_products) if end_products else 0
            total_completed = 0
            
            for ep in end_products:
                completed = db.session.query(
                    func.sum(OperatorLog.run_completed_quantity)
                ).filter(
                    OperatorLog.end_product_sap_id == ep.sap_id,
                    OperatorLog.quality_status == 'approved'
                ).scalar() or 0
                total_completed += completed
            
            completion_rate = (total_completed / total_quantity * 100) if total_quantity > 0 else 0
            project_names.append(project.project_name)
            project_completion.append(completion_rate)
            
            if completion_rate >= 90:
                color = 'rgba(75, 192, 192, 0.5)'
                border = 'rgb(75, 192, 192)'
            elif completion_rate >= 60:
                color = 'rgba(255, 206, 86, 0.5)'
                border = 'rgb(255, 206, 86)'
            else:
                color = 'rgba(255, 99, 132, 0.5)'
                border = 'rgb(255, 99, 132)'
            
            project_colors.append(color)
            project_border_colors.append(border)

            # Get latest end product completion date
            latest_end_product = EndProduct.query.filter_by(project_id=project.id).order_by(EndProduct.completion_date.desc()).first()
            if latest_end_product and latest_end_product.completion_date:
                if latest_end_product.completion_date >= today:
                    on_time_projects += 1
                else:
                    delayed_projects += 1

        # Get machine workload data
        machines = Machine.query.all()
        machine_names = [machine.name for machine in machines]
        machine_workload = []

        for machine in machines:
            # Calculate machine workload based on active jobs
            active_logs = OperatorLog.query.filter(
                OperatorLog.machine_id == machine.id,
                OperatorLog.current_status.in_(['setup', 'cycle_started', 'cycle_paused'])
            ).count()
            workload = (active_logs / 3) * 100 if active_logs > 0 else 0
            machine_workload.append(min(workload, 100))

        # Get production timeline data
        timeline_dates = []
        target_production = []
        actual_production = []
        
        for i in range(30):
            date = (today - timedelta(days=29-i))
            timeline_dates.append(date.strftime('%Y-%m-%d'))
            
            # Get target production for this date
            target = db.session.query(
                func.sum(EndProduct.quantity / 30)  # Assuming even distribution over 30 days
            ).filter(
                EndProduct.completion_date >= date
            ).scalar() or 0
            
            # Get actual production for this date
            actual = db.session.query(
                func.sum(OperatorLog.run_completed_quantity)
            ).filter(
                func.date(OperatorLog.created_at) == date,
                OperatorLog.quality_status == 'approved'
            ).scalar() or 0
            
            target_production.append(round(target))
            actual_production.append(actual)

        # Get machine utilization data
        machine_data = []
        for machine in machines:
            metrics = calculate_machine_utilization(machine)
            current_job = OperatorLog.query.filter_by(
                machine_id=machine.id,
                current_status='running'
            ).order_by(OperatorLog.created_at.desc()).first()

            machine_data.append({
                'name': machine.name,
                'status': machine.status,
                'utilization': metrics['oee'] * 100,  # Convert to percentage
                'current_job': current_job.drawing_number if current_job else None,
                'availability': metrics['availability'],
                'performance': metrics['performance'],
                'quality': metrics['quality']
            })

        # Recent/completed end products
        completed_end_products = (
            db.session.query(EndProduct)
            .outerjoin(OperatorLog, OperatorLog.end_product_sap_id == EndProduct.sap_id)
            .group_by(EndProduct.id)
            .having(func.coalesce(func.sum(OperatorLog.run_completed_quantity), 0) >= EndProduct.quantity)
            .all()
        )

        # Recent quality checks
        recent_quality_checks = QualityCheck.query.order_by(QualityCheck.timestamp.desc()).limit(10).all()

        # Recent/pending rework
        recent_rework = ReworkQueue.query.order_by(ReworkQueue.created_at.desc()).limit(10).all()
        
        # Recent scrap/rejects
        recent_scrap = ScrapLog.query.order_by(ScrapLog.scrapped_at.desc()).limit(10).all()

        # Get bot projects data
        bot_projects = []
        for project in projects:
            components = []
            end_products = EndProduct.query.filter_by(project_id=project.id).all()
            for ep in end_products:
                status = 'Completed'
                if ep.quantity > 0:
                    completed = db.session.query(func.sum(OperatorLog.run_completed_quantity)).filter(
                        OperatorLog.end_product_sap_id == ep.sap_id,
                        OperatorLog.quality_status == 'approved'
                    ).scalar() or 0
                    if completed < ep.quantity:
                        status = 'In Progress' if completed > 0 else 'Not Started'
                components.append({
                    'name': ep.name,
                    'status': status
                })
            
            # Determine overall project status
            if not components:
                project_status = 'Not Started'
            elif all(c['status'] == 'Completed' for c in components):
                project_status = 'Completed'
            else:
                project_status = 'In Progress'
            
            bot_projects.append({
                'name': project.project_name,
                'components': components,
                'status': project_status
            })

        # Get bots data (active operator logs)
        bots = []
        active_logs = OperatorLog.query.filter(
            OperatorLog.current_status.in_(['setup', 'cycle_started', 'cycle_paused'])
        ).all()
        
        for log in active_logs:
            machine = Machine.query.get(log.machine_id)
            operator = User.query.get(log.operator_id) if log.operator_id else None
            end_product = EndProduct.query.filter_by(sap_id=log.end_product_sap_id).first() if log.end_product_sap_id else None
            
            bots.append({
                'bot_id': log.id,
                'cycle_time': log.cycle_time,
                'completion_date': end_product.completion_date if end_product else None,
                'quantity': log.run_planned_quantity,
                'produced': log.run_completed_quantity,
                'machine': machine.name if machine else 'Unknown',
                'tool': log.drawing_number,
                'status': log.current_status,
                'completed_time': log.last_cycle_end_time if log.current_status == 'cycle_completed' else None
            })

        # After processing the uploaded plan and before rendering the template, fetch all end products and their project info for the table
        production_plan_rows = []
        for project in projects:
            end_products = EndProduct.query.filter_by(project_id=project.id).all()
            for ep in end_products:
                # Compute completed quantity
                completed = db.session.query(func.sum(OperatorLog.run_completed_quantity)).filter(
                    OperatorLog.end_product_sap_id == ep.sap_id,
                    OperatorLog.quality_status == 'approved'
                ).scalar() or 0
                # Reconstruct route string
                process_route = ProcessRoute.query.filter_by(sap_id=ep.sap_id).first()
                if process_route:
                    operations = RouteOperation.query.filter_by(route_id=process_route.id).order_by(RouteOperation.sequence).all()
                    route_str = '-'.join([op.operation_type for op in operations])
                else:
                    route_str = ''
                # Compute status
                if completed >= ep.quantity:
                    status = 'Completed'
                elif completed > 0:
                    status = 'In Progress'
                else:
                    status = 'Not Started'
                # Add all columns as dict
                production_plan_rows.append({
                    'project_code': project.project_code,
                    'project_name': project.project_name,
                    'sap_id': ep.sap_id,
                    'end_product': ep.name,
                    'qty': ep.quantity,
                    'st': ep.setup_time_std,
                    'ct': ep.cycle_time_std,
                    'completion_date': ep.completion_date.strftime('%Y-%m-%d') if ep.completion_date else '',
                    'route': route_str,
                    'completed': completed,
                    'status': status
                })

        return render_template('planner_dashboard.html',
            active_projects=active_projects,
            total_drawings=total_drawings,
            on_time_projects=on_time_projects,
            delayed_projects=delayed_projects,
            project_names=project_names,
            project_completion=project_completion,
            project_colors=project_colors,
            project_border_colors=project_border_colors,
            machine_names=machine_names,
            machine_workload=machine_workload,
            timeline_dates=timeline_dates,
            target_production=target_production,
            actual_production=actual_production,
            machine_data=machine_data,
            completed_end_products=completed_end_products,
            recent_quality_checks=recent_quality_checks,
            recent_rework=recent_rework,
            recent_scrap=recent_scrap,
            bot_projects=bot_projects,
            bots=bots,
            production_plan_rows=production_plan_rows
        )
    except Exception as e:
        app.logger.error(f"Error in planner dashboard: {str(e)}")
        flash('An error occurred while loading the dashboard.', 'danger')
        return redirect(url_for('login_general'))

# --- MANAGER ---
@app.route('/upload_drawing_mapping', methods=['POST'])
@login_required
def upload_drawing_mapping():
    """Handle drawing-SAP mapping file upload"""
    if not current_user.is_manager:
        flash('Access denied. Manager access required.', 'danger')
        return redirect(url_for('login_general'))
        
    try:
        if 'drawing_mapping_file' not in request.files:
            flash('No file uploaded.', 'danger')
            return redirect(url_for('manager_dashboard'))
            
        file = request.files['drawing_mapping_file']
        if file.filename == '':
            flash('No file selected.', 'danger')
            return redirect(url_for('manager_dashboard'))
            
        if not file.filename.endswith('.xlsx'):
            flash('Only .xlsx files are allowed.', 'danger')
            return redirect(url_for('manager_dashboard'))
            
        # Read Excel file
        df = pd.read_excel(file)
        
        # Convert column names to lowercase and replace spaces with underscores
        df.columns = df.columns.str.lower().str.replace(' ', '_')
        
        # Try different possible column names
        drawing_col = next((col for col in df.columns if col in ['drawing_number', 'drawing', 'drawingnumber', 'drawing_no', 'drawingno']), None)
        sap_col = next((col for col in df.columns if col in ['sap_id', 'sap', 'sapid']), None)
        
        if not drawing_col or not sap_col:
            flash('Excel file must contain columns for Drawing Number and SAP ID. Please check your column headers.', 'danger')
            return redirect(url_for('manager_dashboard'))
            
        # Process each row
        success_count = 0
        error_count = 0
        
        for _, row in df.iterrows():
            try:
                drawing_number = str(row[drawing_col]).strip()
                sap_id = str(row[sap_col]).strip()
                
                # Skip empty rows
                if not drawing_number or not sap_id or drawing_number.lower() == 'nan' or sap_id.lower() == 'nan':
                    continue
                    
                # Check if drawing exists
                drawing = MachineDrawing.query.filter_by(drawing_number=drawing_number).first()
                
                if drawing:
                    # Update existing drawing
                    drawing.sap_id = sap_id
                else:
                    # Create new drawing
                    drawing = MachineDrawing(
                        drawing_number=drawing_number,
                        sap_id=sap_id
                    )
                    db.session.add(drawing)
                
                success_count += 1
                
            except Exception as e:
                app.logger.error(f"Error processing row {row}: {str(e)}")
                error_count += 1
                continue
                
        db.session.commit()
        
        if error_count == 0:
            flash(f'Successfully processed {success_count} drawings.', 'success')
        else:
            flash(f'Processed {success_count} drawings with {error_count} errors.', 'warning')
            
    except Exception as e:
        app.logger.error(f"Error uploading drawing mapping: {str(e)}")
        flash('An error occurred while processing the file.', 'danger')
        
    return redirect(url_for('manager_dashboard'))

@app.route('/manager', methods=['GET', 'POST'])
@login_required
def manager_dashboard():
    """Handle manager dashboard"""
    if not current_user.is_manager:
        flash('Access denied. Manager access required.', 'danger')
        return redirect(url_for('login_general'))

    try:
        # Get merged data from all tables
        merged_data = db.session.query(
            MachineDrawing,
            EndProduct,
            Project
        ).outerjoin(
            EndProduct, MachineDrawing.sap_id == EndProduct.sap_id
        ).outerjoin(
            Project, EndProduct.project_id == Project.id
        ).order_by(MachineDrawing.drawing_number).all()

        # Get total drawings
        total_drawings = MachineDrawing.query.count()

        # Get active projects (non-deleted projects)
        active_projects = Project.query.filter_by(is_deleted=False).count()

        # Get pending rework
        pending_rework_items = ReworkQueue.query.filter_by(status='pending_manager_approval').all()
        pending_rework_count = len(pending_rework_items)

        # Calculate completion rate based on operator logs
        total_planned = db.session.query(func.sum(EndProduct.quantity)).scalar() or 0
        total_completed = db.session.query(
            func.sum(OperatorLog.run_completed_quantity)
        ).filter(
            OperatorLog.quality_status == 'approved'
        ).scalar() or 0
        completion_rate = (total_completed / total_planned * 100) if total_planned > 0 else 0

        # Get project status data
        projects = Project.query.filter_by(is_deleted=False).all()
        project_data = {
            'project_names': [],
            'completed_counts': [],
            'in_progress_counts': [],
            'pending_counts': []
        }
        
        for project in projects:
            project_data['project_names'].append(project.project_name)
            
            # Get all end products for this project
            end_products = EndProduct.query.filter_by(project_id=project.id).all()
            
            # Initialize counters
            completed = 0
            in_progress = 0
            pending = 0
            
            for ep in end_products:
                # Get operator logs for this end product
                total_quantity = ep.quantity
                completed_quantity = db.session.query(
                    func.sum(OperatorLog.run_completed_quantity)
                ).filter(
                    OperatorLog.end_product_sap_id == ep.sap_id,
                    OperatorLog.quality_status == 'approved'
                ).scalar() or 0
                
                if completed_quantity >= total_quantity:
                    completed += 1
                elif completed_quantity > 0:
                    in_progress += 1
                else:
                    pending += 1
            
            project_data['completed_counts'].append(completed)
            project_data['in_progress_counts'].append(in_progress)
            project_data['pending_counts'].append(pending)

        # Get drawing distribution data
        drawings = MachineDrawing.query.all()
        drawing_categories = ['Mechanical', 'Electrical', 'Assembly', 'Others']
        drawing_counts = [0] * len(drawing_categories)
        
        for drawing in drawings:
            category = get_drawing_category(drawing.drawing_number)
            if category in drawing_categories:
                idx = drawing_categories.index(category)
                drawing_counts[idx] += 1

        # Get production timeline data (last 30 days)
        today = datetime.now(timezone.utc).date()
        start_date = today - timedelta(days=29)
        dates = [(start_date + timedelta(days=x)) for x in range(30)]
        
        timeline = [date.strftime('%Y-%m-%d') for date in dates]
        target_production = []
        actual_production = []
        
        for date in dates:
            # Get target production for this date
            target = db.session.query(
                func.sum(EndProduct.quantity / 30)  # Assuming even distribution over 30 days
            ).filter(
                EndProduct.completion_date >= date
            ).scalar() or 0
            
            # Get actual production for this date
            actual = db.session.query(
                func.sum(OperatorLog.run_completed_quantity)
            ).filter(
                func.date(OperatorLog.created_at) == date,
                OperatorLog.quality_status == 'approved'
            ).scalar() or 0
            
            target_production.append(round(target))
            actual_production.append(actual)

        # Calculate rework data
        rework_data = {
            'approved_count': ReworkQueue.query.filter_by(status='approved').count(),
            'pending_count': ReworkQueue.query.filter_by(status='pending_manager_approval').count(),
            'rejected_count': ReworkQueue.query.filter_by(status='rejected').count()
        }

        return render_template(
            'manager_dashboard.html',
            merged_data=merged_data,
            total_drawings=total_drawings,
            active_projects=active_projects,
            pending_rework=pending_rework_items,
            pending_rework_count=pending_rework_count,
            completion_rate=round(completion_rate, 2),
            project_data=project_data,
            drawing_categories=drawing_categories,
            drawing_counts=drawing_counts,
            timeline=timeline,
            target_production=target_production,
            actual_production=actual_production,
            rework_data=rework_data
        )

    except Exception as e:
        app.logger.error(f"Error in manager dashboard: {str(e)}")
        flash('An error occurred while loading the dashboard.', 'danger')
        return redirect(url_for('login_general'))

@with_db_session
def emit_manager_update():
    """Emit manager dashboard update data"""
    try:
        # Get total drawings
        total_drawings = MachineDrawing.query.count()

        # Get active projects
        active_projects = Project.query.filter(
            Project.status.in_(['active', 'in_progress'])
        ).count()

        # Get pending rework
        pending_rework = ReworkQueue.query.filter_by(
            status='pending_manager_approval'
        ).count()

        # Calculate completion rate
        total_products = EndProduct.query.count()
        completed_products = EndProduct.query.filter(
            EndProduct.status == 'completed'
        ).count()
        completion_rate = completed_products / total_products if total_products > 0 else 0

        # Get project status data
        projects = Project.query.all()
        project_data = {
            'project_names': [],
            'completed_counts': [],
            'in_progress_counts': [],
            'pending_counts': []
        }
        
        for project in projects:
            project_data['project_names'].append(project.project_name)
            
            # Count products by status
            products = EndProduct.query.filter_by(project_id=project.id).all()
            completed = sum(1 for p in products if p.status == 'completed')
            in_progress = sum(1 for p in products if p.status == 'in_progress')
            pending = sum(1 for p in products if p.status == 'pending')
            
            project_data['completed_counts'].append(completed)
            project_data['in_progress_counts'].append(in_progress)
            project_data['pending_counts'].append(pending)

        # Get drawing distribution data
        drawings = MachineDrawing.query.all()
        drawing_categories = ['Mechanical', 'Electrical', 'Assembly', 'Others']
        drawing_counts = [0] * len(drawing_categories)
        
        for drawing in drawings:
            category = get_drawing_category(drawing.drawing_number)
            if category in drawing_categories:
                idx = drawing_categories.index(category)
                drawing_counts[idx] += 1

        # Get production timeline data
        timeline = []
        target_production = []
        actual_production = []
        
        for i in range(7):  # Last 7 days
            date = datetime.now() - timedelta(days=i)
            timeline.append(date.strftime('%Y-%m-%d'))
            
            # Get target and actual production for this date
            daily_target = db.session.query(func.sum(EndProduct.quantity))\
                .filter(EndProduct.completion_date == date.date())\
                .scalar() or 0
                
            daily_actual = db.session.query(func.sum(OperatorLog.run_completed_quantity))\
                .filter(
                    func.date(OperatorLog.created_at) == date.date(),
                    OperatorLog.current_status == 'completed'
                ).scalar() or 0
            
            target_production.append(daily_target)
            actual_production.append(daily_actual)

        # Calculate quality metrics
        total_production = db.session.query(func.sum(OperatorLog.run_completed_quantity))\
            .filter(OperatorLog.current_status == 'completed')\
            .scalar() or 0
            
        total_rework = db.session.query(func.sum(
            OperatorLog.run_rework_quantity_fpi + OperatorLog.run_rework_quantity_lpi))\
            .scalar() or 0
            
        total_rejected = db.session.query(func.sum(
            OperatorLog.run_rejected_quantity_fpi + OperatorLog.run_rejected_quantity_lpi))\
            .scalar() or 0

        if total_production > 0:
            first_pass_yield = ((total_production - total_rework - total_rejected) / total_production) * 100
            rework_rate = (total_rework / total_production) * 100
            rejection_rate = (total_rejected / total_production) * 100
        else:
            first_pass_yield = rework_rate = rejection_rate = 0

        # Emit data via Socket.IO
        socketio.emit('manager_update', {
            'total_drawings': total_drawings,
            'active_projects': active_projects,
            'pending_rework': pending_rework,
            'completion_rate': completion_rate,
            'project_names': project_data['project_names'],
            'completed_counts': project_data['completed_counts'],
            'in_progress_counts': project_data['in_progress_counts'],
            'pending_counts': project_data['pending_counts'],
            'drawing_categories': drawing_categories,
            'drawing_counts': drawing_counts,
            'timeline': timeline,
            'target_production': target_production,
            'actual_production': actual_production,
            'first_pass_yield': first_pass_yield,
            'rework_rate': rework_rate,
            'rejection_rate': rejection_rate
        })

    except Exception as e:
        app.logger.error(f"Error emitting manager update: {str(e)}")

def get_drawing_category(drawing_number):
    """Determine drawing category based on drawing number prefix"""
    if drawing_number.startswith('M'):
        return 'Mechanical'
    elif drawing_number.startswith('E'):
        return 'Electrical'
    elif drawing_number.startswith('A'):
        return 'Assembly'
    else:
        return 'Others'

@app.route('/manager/approve_rework/<int:rework_id>', methods=['POST'])
@login_required
def approve_rework(rework_id):
    """Handle rework approval by manager"""
    if not current_user.is_manager:
        flash('Access denied. Manager access required.', 'danger')
        return redirect(url_for('login_general'))

    try:
        rework_item = ReworkQueue.query.get_or_404(rework_id)
        action = request.form.get('action')
        if not action:
            flash('No action specified.', 'danger')
            return redirect(url_for('manager_dashboard'))

        if action == 'approve':
            rework_item.status = 'manager_approved'
            rework_item.approved_by = getattr(current_user, 'username', None)
            rework_item.approved_at = datetime.now(timezone.utc)
            flash('Rework request approved.', 'success')
        elif action == 'reject':
            rework_item.status = 'manager_rejected'
            rework_item.rejected_by = getattr(current_user, 'username', None)
            rework_item.rejected_at = datetime.now(timezone.utc)
            rework_item.rejection_reason = request.form.get('rejection_reason')
            flash('Rework request rejected.', 'warning')
        else:
            flash('Invalid action.', 'danger')
            return redirect(url_for('manager_dashboard'))
        db.session.commit()
        return redirect(url_for('manager_dashboard'))

    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error in rework approval: {str(e)}")
        flash('An error occurred while processing the rework request.', 'danger')
        return redirect(url_for('manager_dashboard'))



def get_redirect_url(machine_name):
    """Get the correct redirect URL for a machine"""
    try:
        # Get machine to verify it exists
        machine = Machine.query.filter_by(name=machine_name).first()
        if not machine:
            flash(f'Machine {machine_name} not found.', 'warning')
            return url_for('operator_login')
            
        # Use the generic operator panel for all machines
        return url_for('operator_panel', machine_name=machine_name)
            
    except Exception as e:
        app.logger.error(f"Error getting redirect URL: {str(e)}")
        flash('Error accessing machine panel.', 'danger')
        return url_for('operator_login')

def operator_panel_common(machine_name, template_name):
    """Common function for operator panels"""
    try:
        # Get machine
        machine = Machine.query.filter_by(name=machine_name).first()
        if not machine:
            flash('Machine not found', 'danger')
            return redirect(url_for('home'))

        # Get active operator session
        active_session = OperatorSession.query.filter_by(
            machine_id=machine.id,
            is_active=True
        ).first()

        # Get available assignments and assigned drawings
        available_assignments = MachineDrawingAssignment.query.filter(
            MachineDrawingAssignment.machine_id == machine.id
        ).all()
        assigned_drawings = [a.drawing_rel for a in available_assignments]

        # Determine active_drawing from session
        active_drawing = None
        drawing_number = session.get('current_drawing_number')
        if drawing_number:
            for d in assigned_drawings:
                if d.drawing_number.strip() == drawing_number.strip():
                    active_drawing = d
                    break

        # Get current log
        current_log = None
        if active_session:
            current_log = OperatorLog.query.filter(
                OperatorLog.operator_session_id == active_session.id,
                OperatorLog.current_status.notin_(['cycle_completed', 'admin_closed'])
            ).order_by(OperatorLog.created_at.desc()).first()

        # --- Robust Operator Action Handling with Debug ---
        if request.method == 'POST':
            action = request.form.get('action')
            form_drawing_number = request.form.get('drawing_number_input', '').strip()
            print("POST action:", action)
            print("Drawing number from form:", repr(form_drawing_number))
            print("Assigned drawings:", [repr(d.drawing_number) for d in assigned_drawings])
            print("Active session:", active_session)

            # Handle machine breakdown actions
            if action == 'report_breakdown':
                # Create new breakdown log
                breakdown = MachineBreakdownLog(
                    machine_id=machine.id,
                    breakdown_start_time=datetime.now(timezone.utc),
                    breakdown_type='unplanned',
                    reason='Operator reported breakdown',
                    is_active=True
                )
                # Update machine status
                machine.status = 'breakdown'
                db.session.add(breakdown)
                db.session.commit()
                flash('Machine breakdown reported. All production actions are disabled.', 'warning')
                return redirect(request.url)
            elif action == 'mark_machine_healthy':
                # Find active breakdown and mark it as resolved
                active_breakdown = MachineBreakdownLog.query.filter_by(
                    machine_id=machine.id,
                    is_active=True
                ).first()
                if active_breakdown:
                    active_breakdown.is_active = False
                    active_breakdown.breakdown_end_time = datetime.now(timezone.utc)
                    machine.status = 'operational'
                    db.session.commit()
                    flash('Machine marked as healthy. Production actions are now enabled.', 'success')
                return redirect(request.url)

            if action == 'select_drawing_and_start_session':
                if form_drawing_number:
                    session['current_drawing_number'] = form_drawing_number
                    session.modified = True
                    print("Session drawing set to:", form_drawing_number)
                return redirect(request.url)
            drawing = next((d for d in assigned_drawings if d.drawing_number.strip() == form_drawing_number), None)
            if action == 'start_setup':
                if not drawing:
                    flash('Drawing not found or not assigned to this machine.', 'danger')
                    print("Drawing not found or not assigned.")
                    return redirect(request.url)
                if not active_session:
                    flash('No active operator session.', 'danger')
                    print("No active operator session.")
                    return redirect(request.url)
                # Only allow setup if no active log for this drawing, or if a different drawing was made in between
                last_log = OperatorLog.query.filter(
                    OperatorLog.operator_session_id == active_session.id
                ).order_by(OperatorLog.created_at.desc()).first()
                if last_log and last_log.drawing_id == drawing.id and last_log.current_status not in ['cycle_completed', 'lpi_completed', 'admin_closed']:
                    flash('Setup already done for this drawing. Switch to another drawing to reset.', 'warning')
                    print('Setup already done for this drawing. Blocked.')
                    return redirect(request.url)
                batch_id = request.form.get('batch_id')
                if not batch_id:
                    batch_id = generate_batch_id(form_drawing_number)
                # Find the most recent assignment for this machine/drawing
                assignment = MachineDrawingAssignment.query.filter_by(
                    machine_id=machine.id,
                    drawing_id=drawing.id,
                    status='assigned'
                ).order_by(MachineDrawingAssignment.assigned_at.desc()).first()
                new_log = OperatorLog(
                    operator_session_id=active_session.id,
                    drawing_id=drawing.id,
                    current_status='setup_started',
                    run_planned_quantity=0,
                    run_completed_quantity=0,
                    batch_id=batch_id,
                    assignment_id=assignment.id if assignment else None
                )
                db.session.add(new_log)
                db.session.commit()
                flash(f'Setup started for drawing {form_drawing_number}', 'success')
                print(f'Setup started for drawing {form_drawing_number}')
                return redirect(request.url)
            elif action == 'setup_done' and current_log and current_log.current_status == 'setup_started':
                current_log.current_status = 'setup_done'
                db.session.commit()
                flash('Setup marked as done.', 'success')
                print('Setup marked as done.')
                return redirect(request.url)
            elif action == 'cancel_current_drawing_log' and current_log:
                current_log.current_status = 'admin_closed'
                db.session.commit()
                flash('Current log cancelled.', 'info')
                print('Current log cancelled.')
                return redirect(request.url)
            elif action == 'cycle_start':
                # Only block if FPI is pending after first piece (i.e., after first cycle_completed_pending_fpi)
                if current_log and current_log.current_status == 'cycle_completed_pending_fpi' and current_log.fpi_status not in ['pass', 'passed']:
                    flash('FPI must be passed before starting production cycle for this batch.', 'danger')
                    return redirect(request.url)
                if current_log and current_log.current_status in ['setup_done', 'fpi_passed_ready_for_cycle', 'cycle_paused']:
                    current_log.current_status = 'cycle_started'
                    current_log.cycle_start_time = datetime.now(timezone.utc)
                    db.session.commit()
                    flash('Cycle started.', 'success')
                return redirect(request.url)
            elif action == 'cycle_complete':
                if current_log and current_log.current_status == 'cycle_started':
                    is_first_piece = (current_log.run_completed_quantity == 0)
                    is_last_piece = (current_log.run_completed_quantity + 1 >= current_log.run_planned_quantity)
                    # Check if LPI is required for this product
                    lpi_required = False
                    if current_log.drawing_rel and current_log.drawing_rel.end_product_rel:
                        lpi_required = getattr(current_log.drawing_rel.end_product_rel, 'is_last_piece_lpi_required', False)
                    if not is_first_piece and current_log.fpi_status not in ['pass', 'passed']:
                        flash('FPI must be passed before completing cycle for this batch.', 'danger')
                        return redirect(request.url)
                    current_log.run_completed_quantity += 1
                    if is_first_piece:
                        current_log.current_status = 'cycle_completed_pending_fpi'
                        db.session.commit()
                        flash('First piece completed. Awaiting FPI (First Piece Inspection).', 'info')
                        return redirect(request.url)
                    elif is_last_piece and lpi_required:
                        current_log.current_status = 'lpi_pending'
                        db.session.commit()
                        flash('Last piece completed. Awaiting LPI (Last Piece Inspection).', 'info')
                        return redirect(request.url)
                    else:
                        current_log.current_status = 'cycle_completed'
                        db.session.commit()
                        flash('Cycle completed.', 'success')
                return redirect(request.url)
            elif action == 'cycle_pause' and current_log and current_log.current_status == 'cycle_started':
                current_log.current_status = 'cycle_paused'
                db.session.commit()
                flash('Cycle paused.', 'info')
                print('Cycle paused.')
                return redirect(request.url)

        # Get quality checks
        quality_checks = []
        if current_log:
            quality_checks = QualityCheck.query.filter_by(
                operator_log_id=current_log.id
            ).order_by(QualityCheck.timestamp.desc()).all()

        # Get rework items
        rework_items = []
        if current_log:
            rework_items = ReworkQueue.query.filter_by(
                source_operator_log_id=current_log.id
            ).order_by(ReworkQueue.created_at.desc()).all()

        # Get machine breakdowns
        breakdowns = MachineBreakdownLog.query.filter_by(
            machine_id=machine.id
        ).order_by(MachineBreakdownLog.created_at.desc()).limit(5).all()

        # Get production records
        production_records = ProductionRecord.query.filter_by(
            machine_id=machine.id
        ).order_by(ProductionRecord.timestamp.desc()).limit(10).all()

        # Calculate metrics
        metrics = {
            'availability': calculate_availability(machine),
            'performance': calculate_performance(machine),
            'quality': calculate_quality(machine),
            'oee': calculate_machine_oee(machine.id)
        }

        # Check if machine is operational
        machine_operational = not any(b.is_active for b in breakdowns)

        # Build assignment rows for the table
        assignment_rows = []
        for assignment in available_assignments:
            completed = db.session.query(func.sum(OperatorLog.run_completed_quantity)).filter(
                OperatorLog.machine_id == assignment.machine_id,
                OperatorLog.drawing_id == assignment.drawing_id,
                OperatorLog.quality_status == 'approved'
            ).scalar() or 0
            assignment_rows.append({
                'drawing_number': assignment.drawing_rel.drawing_number,
                'tool_number': assignment.tool_number,
                'assigned_quantity': assignment.assigned_quantity,
                'completed_quantity': completed,
                'status': assignment.status,
                'id': assignment.id
            })
        print('assignment_rows:', assignment_rows)

        print('current_log:', current_log)
        if current_log:
            print('current_log.status:', current_log.current_status)
            print('current_log.drawing_id:', current_log.drawing_id)
            print('active_drawing.id:', active_drawing.id if active_drawing else None)

        # --- Add detailed drawing status for selected drawing ---
        assigned_qty = completed_qty = passed_qty = fpi_status = lpi_status = None
        if active_drawing:
            # Assigned quantity for this machine/drawing
            assignment = MachineDrawingAssignment.query.filter_by(
                machine_id=machine.id, drawing_id=active_drawing.id, status='assigned'
            ).first()
            assigned_qty = assignment.assigned_quantity if assignment else 0
            # Completed quantity for this machine/drawing
            completed_qty = db.session.query(db.func.sum(OperatorLog.run_completed_quantity)).filter(
                OperatorLog.machine_id == machine.id,
                OperatorLog.drawing_id == active_drawing.id
            ).scalar() or 0
            # Passed quantity (all quality checks approved)
            passed_qty = db.session.query(db.func.sum(OperatorLog.run_completed_quantity)).filter(
                OperatorLog.machine_id == machine.id,
                OperatorLog.drawing_id == active_drawing.id,
                OperatorLog.quality_status == 'approved'
            ).scalar() or 0
            # Latest FPI/LPI status
            latest_log = OperatorLog.query.filter_by(
                machine_id=machine.id, drawing_id=active_drawing.id
            ).order_by(OperatorLog.created_at.desc()).first()
            fpi_status = latest_log.fpi_status if latest_log else 'N/A'
            lpi_status = latest_log.lpi_status if latest_log else 'N/A'

        # Calculate parts_today
        today = datetime.now(timezone.utc).date()
        parts_today = db.session.query(func.sum(OperatorLog.run_completed_quantity)).filter(
            OperatorLog.machine_id == machine.id,
            OperatorLog.quality_status == 'approved',
            func.date(OperatorLog.created_at) == today
        ).scalar() or 0

        # Calculate uptime (machine hours today)
        uptime = 0.0
        if active_session and active_session.start_time:
            session_start = active_session.start_time
            now = datetime.now(timezone.utc)
            # Make session_start timezone-aware if it's naive
            if session_start.tzinfo is None:
                session_start = session_start.replace(tzinfo=timezone.utc)
            if session_start.date() == today:
                uptime = (now - session_start).total_seconds() / 3600.0

        return render_template(template_name,
            machine=machine,
            current_machine_obj=machine,  # Pass the machine object for template logic
            active_session=active_session,
            current_log=current_log,
            available_assignments=available_assignments,
            assigned_drawings=assigned_drawings,
            active_drawing=active_drawing,
            quality_checks=quality_checks,
            rework_items=rework_items,
            breakdowns=breakdowns,
            production_records=production_records,
            metrics=metrics,
            assignment_rows=assignment_rows,
            machine_operational=machine_operational,
            # New context for drawing status
            assigned_qty=assigned_qty,
            completed_qty=completed_qty,
            passed_qty=passed_qty,
            fpi_status=fpi_status,
            lpi_status=lpi_status,
            parts_today=parts_today,
            uptime=uptime
        )

    except Exception as e:
        app.logger.error(f"Error in operator panel: {str(e)}")
        flash('Error loading operator panel', 'danger')
        return redirect(url_for('home'))

@app.route('/operator_logout', methods=['POST'])
def operator_logout():
    """Handle operator logout and cleanup session data"""
    try:
        now = datetime.now(timezone.utc)
        operator_session_id = session.get('operator_session_id')
        
        if operator_session_id:
            op_session = db.session.get(OperatorSession, operator_session_id)
            if op_session and op_session.is_active:
                # Update session end time
                op_session.is_active = False
                op_session.logout_time = now
                op_session.end_time = now
                
                # Calculate session metrics
                if op_session.login_time:
                    session_duration = (now - ensure_timezone_aware(op_session.login_time)).total_seconds() / 3600.0
                    op_session.total_cycle_time = session_duration
                
                # Close any hanging logs
                hanging_logs = OperatorLog.query.filter(
                    OperatorLog.operator_session_id == operator_session_id,
                    OperatorLog.current_status.notin_(['lpi_completed', 'admin_closed'])
                ).all()
                
                for log in hanging_logs:
                    log.current_status = 'admin_closed'
                    log.notes = (log.notes or "") + f"\nLog auto-closed due to operator logout at {now}."
                    
                    # Calculate log metrics if possible
                    if log.setup_start_time and not log.setup_end_time:
                        log.setup_end_time = now
                        setup_time = (now - ensure_timezone_aware(log.setup_start_time)).total_seconds() / 60.0
                        log.setup_time = setup_time
                        
                    if log.cycle_start_time and not log.last_cycle_end_time:
                        log.last_cycle_end_time = now
                        cycle_time = (now - ensure_timezone_aware(log.cycle_start_time)).total_seconds() / 60.0
                        log.cycle_time = cycle_time
                
                db.session.commit()
                
                # Update machine status if needed
                machine = Machine.query.get(op_session.machine_id)
                if machine and machine.status not in ['breakdown', 'maintenance']:
                    machine.status = 'idle'
                    db.session.commit()
                    emit_digital_twin_update(machine)

        # Clear all session data
        clear_user_session()
        logout_user()

        flash('You have been logged out from Operator station.', 'info')
        return redirect(url_for('operator_login'))
        
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error during operator logout: {str(e)}")
        app.logger.error(f"Stack trace: {traceback.format_exc()}")
        flash('An error occurred during logout.', 'danger')
        return redirect(url_for('operator_login'))

@app.route('/operator_login')
def operator_login():
    """Show operator login page with all available machines"""
    try:
        # Get all machines from database
        machines = Machine.query.all()
        return render_template('operator_login.html', machines=machines)
    except Exception as e:
        app.logger.error(f"Error loading operator login: {str(e)}")
        flash('Error loading machines.', 'danger')
        return redirect(url_for('index'))

@app.route('/select_machine/<machine_name>')
def select_machine(machine_name):
    """Show operator login form for selected machine"""
    try:
        machine = Machine.query.filter_by(name=machine_name).first()
        if not machine:
            flash(f'Machine {machine_name} not found.', 'warning')
            return redirect(url_for('operator_login'))
            
        return render_template(
            'operator_login_form.html',
            machine=machine
        )
    except Exception as e:
        app.logger.error(f"Error selecting machine: {str(e)}")
        flash('Error selecting machine.', 'danger')
        return redirect(url_for('operator_login'))

# ... rest of the code remains unchanged ...

def calculate_performance(machine):
    try:
        logs = OperatorLog.query.join(OperatorSession).filter(
            OperatorSession.machine_id == machine.id
        ).all()
        if not logs:
            return 0
        total_cycle_time = sum(log.cycle_time or 0 for log in logs)
        total_actual_time = sum(
            (log.last_cycle_end_time - log.first_cycle_start_time).total_seconds() / 60
            for log in logs if log.last_cycle_end_time and log.first_cycle_start_time
        )
        if total_actual_time == 0:
            return 0
        return max(0, min(1, total_cycle_time / total_actual_time))
    except Exception as e:
        app.logger.error(f"Error calculating performance: {str(e)}")
        return 0

def calculate_quality(machine):
    try:
        logs = OperatorLog.query.join(OperatorSession).filter(
            OperatorSession.machine_id == machine.id
        ).all()
        if not logs:
            return 0
        total_parts = sum(log.run_completed_quantity or 0 for log in logs)
        rejected_parts = sum(
            (log.run_rejected_quantity_fpi or 0) + (log.run_rejected_quantity_lpi or 0)
            for log in logs
        )
        if total_parts == 0:
            return 0
        return max(0, min(1, (total_parts - rejected_parts) / total_parts))
    except Exception as e:
        app.logger.error(f"Error calculating quality: {str(e)}")
        return 0

def calculate_availability(machine):
    try:
        today = datetime.now(timezone.utc).date()
        total_time = 8 * 60 * 60  # 8 hours in seconds
        
        # Get downtime from breakdowns
        downtime = db.session.query(func.sum(
            func.coalesce(
                func.extract('epoch', MachineBreakdownLog.breakdown_end_time) -
                func.extract('epoch', MachineBreakdownLog.breakdown_start_time),
                0
            )
        )).filter(
            MachineBreakdownLog.machine_id == machine.id,
            func.date(MachineBreakdownLog.breakdown_start_time) == today
        ).scalar() or 0
        
        available_time = total_time - downtime
        return safe_division(available_time, total_time, 0)
    except Exception as e:
        app.logger.error(f"Error calculating availability for machine {machine.id}: {str(e)}")
        return 0

def calculate_machine_utilization(machine):
    """Calculate utilization metrics for a specific machine"""
    try:
        today = datetime.now(timezone.utc).date()
        
        # Get total time in minutes for today
        total_time = 24 * 60  # minutes in a day
        
        # Get all types of downtime
        planned_downtime = db.session.query(
            func.sum(OperatorLog.downtime_minutes)
        ).filter(
            OperatorLog.machine_id == str(machine.id),
            func.date(OperatorLog.created_at) == today,
            OperatorLog.downtime_category == 'planned'
        ).scalar() or 0
        
        unplanned_downtime = db.session.query(
            func.sum(OperatorLog.downtime_minutes)
        ).filter(
            OperatorLog.machine_id == str(machine.id),
            func.date(OperatorLog.created_at) == today,
            OperatorLog.downtime_category == 'unplanned'
        ).scalar() or 0
        
        maintenance_downtime = db.session.query(
            func.sum(OperatorLog.downtime_minutes)
        ).filter(
            OperatorLog.machine_id == str(machine.id),
            func.date(OperatorLog.created_at) == today,
            OperatorLog.downtime_category == 'maintenance'
        ).scalar() or 0
        
        # Calculate setup time
        setup_time = db.session.query(
            func.sum(
                case(
                    (
                        OperatorLog.setup_end_time.isnot(None) & 
                        OperatorLog.setup_start_time.isnot(None),
                        func.extract('epoch', OperatorLog.setup_end_time - OperatorLog.setup_start_time) / 60
                    ),
                    else_=0
                )
            )
        ).filter(
            OperatorLog.machine_id == str(machine.id),
            func.date(OperatorLog.created_at) == today
        ).scalar() or 0
        
        # Total downtime is sum of all downtimes
        total_downtime = planned_downtime + unplanned_downtime + maintenance_downtime + setup_time
        
        # Calculate utilization
        utilization = ((total_time - total_downtime) / total_time) * 100
        
        # Calculate individual metrics for OEE
        availability = calculate_availability(machine)
        performance = calculate_performance(machine)
        quality = calculate_quality(machine)
        
        # Calculate OEE (Overall Equipment Effectiveness)
        oee = (availability * performance * quality) / 10000  # Convert from percentage
        
        # Log the metrics for debugging
        app.logger.debug(f"Machine {machine.name} metrics - A: {availability:.2f}%, P: {performance:.2f}%, Q: {quality:.2f}%, OEE: {oee:.2f}, Utilization: {utilization:.2f}%")
        
        return {
            'availability': availability,
            'performance': performance,
            'quality': quality,
            'oee': oee,
            'utilization': utilization
        }
    except Exception as e:
        app.logger.error(f"Error calculating machine utilization for {machine.name}: {str(e)}")
        return {
            'availability': 0,
            'performance': 0,
            'quality': 0,
            'oee': 0,
            'utilization': 0
        }

def calculate_overall_machine_utilization():
    """Calculate overall machine utilization rate"""
    try:
        machines = Machine.query.all()
        if not machines:
            return 0.0
            
        active_machines = len([m for m in machines if m.status in ['in_use', 'running', 'cycle_started']])
        total_machines = len(machines)
        
        return active_machines / total_machines if total_machines > 0 else 0.0
    except Exception as e:
        app.logger.error(f"Error calculating machine utilization: {str(e)}")
        return 0.0

@socketio.on('connect')
def handle_connect():
    try:
        if current_user.is_authenticated:
            client_id = f'user_{current_user.id}'
            if socket_rate_limiter.is_allowed(client_id):
                join_room(client_id)
                if current_user.role == 'manager':
                    join_room('manager_room')
                elif current_user.role == 'plant_head':
                    join_room('plant_head_room')
                app.logger.info(f'User {current_user.id} connected')
            else:
                raise ConnectionRefusedError('Rate limit exceeded')
        else:
            raise ConnectionRefusedError('Authentication required')
    except Exception as e:
        app.logger.error(f'Socket connection error: {str(e)}')
        raise ConnectionRefusedError(str(e))

@socketio.on('disconnect')
def handle_disconnect():
    try:
        if current_user.is_authenticated:
            client_id = f'user_{current_user.id}'
            leave_room(client_id)
            if current_user.role == 'manager':
                leave_room('manager_room')
            elif current_user.role == 'plant_head':
                leave_room('plant_head_room')
            app.logger.info(f'User {current_user.id} disconnected')
    except Exception as e:
        app.logger.error(f'Socket disconnection error: {str(e)}')

@app.route('/operator_login_submit', methods=['POST'])
def operator_login_submit():
    """Handle operator login form submission"""
    try:
        operator_name = request.form.get('operator_name', '').strip()
        machine_name = request.form.get('machine_name')
        shift = request.form.get('shift')
        
        if not operator_name or not machine_name or not shift:
            flash('All fields are required for login.', 'danger')
            return redirect(url_for('operator_login'))
        
        # Verify machine exists
        machine = Machine.query.filter_by(name=machine_name).first()
        if not machine:
            flash(f'Machine {machine_name} not found.', 'danger')
            return redirect(url_for('operator_login'))
            
        # Validate shift timing
        current_hour = datetime.now(timezone.utc).hour
        valid_shift = True
        shift_message = None
        
        if shift == 'First' and (current_hour < 6 or current_hour >= 14):
            valid_shift = False
            shift_message = 'First shift is from 6 AM to 2 PM'
        elif shift == 'Second' and (current_hour < 14 or current_hour >= 22):
            valid_shift = False
            shift_message = 'Second shift is from 2 PM to 10 PM'
        elif shift == 'Third' and (current_hour >= 6 and current_hour < 22):
            valid_shift = False
            shift_message = 'Third shift is from 10 PM to 6 AM'
            
        if not valid_shift:
            flash(f'Invalid shift selection. {shift_message}.', 'danger')
            return redirect(url_for('operator_login'))
            
        # End any previous active sessions
        old_sessions = OperatorSession.query.filter(
            db.or_(
                OperatorSession.operator_name == operator_name,
                OperatorSession.machine_id == machine.id
            ),
            OperatorSession.is_active == True
        ).all()
        
        now = datetime.now(timezone.utc)
        for old_session in old_sessions:
            old_session.is_active = False
            old_session.logout_time = now
            old_session.end_time = now
            
            # Close any hanging logs
            hanging_logs = OperatorLog.query.filter(
                OperatorLog.operator_session_id == old_session.id,
                OperatorLog.current_status.notin_(['lpi_completed', 'admin_closed'])
            ).all()
            
            for log in hanging_logs:
                log.current_status = 'admin_closed'
                log.notes = (log.notes or "") + f"\nLog auto-closed due to new operator login at {now}."
        
        db.session.commit()
        
        # Create new session with proper timezone handling
        new_op_session = OperatorSession(
            operator_name=operator_name,
            machine_id=machine.id,
            shift=shift,
            is_active=True,
            login_time=now,
            start_time=now
        )
        db.session.add(new_op_session)
        db.session.commit()
        
        # Set session data
        session['active_role'] = 'operator'
        session['operator_session_id'] = new_op_session.id
        session['operator_name'] = operator_name
        session['machine_name'] = machine_name
        session['shift'] = shift
        session['login_time'] = now.isoformat()
        
        # Create user object and login
        user = SimpleUser(f"{operator_name}|operator", 'operator')
        login_user(user)
        
        # Restore previous session state
        previous_state = restore_operator_session(operator_name, machine_name)
        if previous_state:
            session['current_drawing_id'] = previous_state['drawing_id']
            flash(f'Previous session state restored. Last drawing had {previous_state["completed_quantity"]}/{previous_state["planned_quantity"]} parts completed.', 'info')
        
        flash(f'Welcome {operator_name}! Logged in on {machine_name}, Shift {shift}.', 'success')
        
        # Emit update for the machine
        machine = Machine.query.filter_by(name=machine_name).first()
        if machine:
            emit_digital_twin_update(machine)
        
        return redirect(url_for('operator_panel', machine_name=machine_name))
        
    except Exception as e:
        db.session.rollback()
        error_msg = f"Error during operator login: {str(e)}"
        app.logger.error(error_msg)
        flash('An error occurred during login. Please try again.', 'danger')
        return redirect(url_for('operator_login'))



def get_redirect_url(machine_name):
    """Get the correct redirect URL for a machine"""
    try:
        # Get machine to verify it exists
        machine = Machine.query.filter_by(name=machine_name).first()
        if not machine:
            flash(f'Machine {machine_name} not found.', 'warning')
            return url_for('operator_login')
            
        # Use the generic operator panel for all machines
        return url_for('operator_panel', machine_name=machine_name)
            
    except Exception as e:
        app.logger.error(f"Error getting redirect URL: {str(e)}")
        flash('Error accessing machine panel.', 'danger')
        return url_for('operator_login')

def calculate_machine_oee(machine_id):
    """Calculate Overall Equipment Effectiveness (OEE) for a specific machine"""
    try:
        machine = Machine.query.get(machine_id)
        if not machine:
            return 0.0
            
        # Calculate individual components
        availability = calculate_availability(machine)
        performance = calculate_performance(machine)
        quality = calculate_quality(machine)
        
        # Calculate OEE (multiply components and convert to percentage)
        oee = (availability * performance * quality)
        
        # Log the calculation for debugging
        app.logger.debug(f"Machine {machine.name} OEE calculation:")
        app.logger.debug(f"Availability: {availability:.2f}")
        app.logger.debug(f"Performance: {performance:.2f}")
        app.logger.debug(f"Quality: {quality:.2f}")
        app.logger.debug(f"OEE: {oee:.2f}")
        
        return oee
    except Exception as e:
        app.logger.error(f"Error calculating OEE for machine {machine_id}: {str(e)}")
        return 0.0

def ensure_timezone_aware(dt):
    """Ensure a datetime is timezone aware, converting to UTC if naive"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt

def calculate_uptime(session_start):
    """Calculate uptime in hours from a session start time"""
    if not session_start:
        return 0.0
    
    now = datetime.now(timezone.utc)
    session_start = ensure_timezone_aware(session_start)
    
    # Only count uptime for today
    today = now.date()
    if session_start.date() != today:
        return 0.0
        
    return (now - session_start).total_seconds() / 3600.0

@app.route('/digital_twin')
@login_required
def digital_twin_dashboard():
    # Allow both plant_head and manager roles
    if not (current_user.is_plant_head or current_user.is_manager):
        flash('Access denied. Plant Head or Manager access required.', 'danger')
        return redirect(url_for('login_general'))
    try:
        machines = Machine.query.all()
        machine_data = []
        now = datetime.now(timezone.utc)
        total_machines = len(machines)
        active_machines = 0
        overall_oee = 0
        todays_production = 0
        quality_rate = 0
        for machine in machines:
            # Get all sessions for today
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            sessions_today = OperatorSession.query.filter(
                OperatorSession.machine_id == machine.id,
                OperatorSession.start_time >= today_start
            ).all()
            status_duration = 0.0
            for session in sessions_today:
                start = ensure_timezone_aware(session.start_time)
                end = ensure_timezone_aware(session.end_time) if session.end_time else now
                status_duration += (end - start).total_seconds() / 3600
            status_duration = round(status_duration, 1)
            current_session = OperatorSession.query.filter_by(
                machine_id=machine.id,
                end_time=None
            ).first()
            current_operator = None
            if current_session:
                current_operator = {
                    'name': current_session.operator_name,
                    'shift': current_session.shift
                }
            # Find latest OperatorLog for this session
            current_log = None
            if current_session:
                current_log = OperatorLog.query.filter_by(
                    operator_session_id=current_session.id
                ).order_by(OperatorLog.created_at.desc()).first()
            # Determine status
            status = 'offline'
            if current_session:
                if current_log and current_log.current_status in ['cycle_started', 'cycle_completed']:
                    status = 'running'
                    active_machines += 1
                elif current_log and current_log.current_status == 'setup_started':
                    status = 'setup'
                else:
                    status = 'idle'
            # OEE and metrics
            availability = calculate_availability(machine)
            performance = calculate_performance(machine)
            quality = calculate_quality(machine)
            machine_oee = (availability * performance * quality) / 10000
            current_job = None
            if current_log and current_log.drawing_rel:
                current_job = {
                    'drawing_number': current_log.drawing_rel.drawing_number,
                    'sap_id': current_log.drawing_rel.sap_id,
                    'product_name': current_log.end_product_sap_id_rel.name if current_log.end_product_sap_id_rel else 'Unknown'
                }
            machine_metrics = {
                'oee': {
                    'availability': availability,
                    'performance': performance,
                    'quality': quality,
                    'overall': machine_oee * 100
                },
                'name': machine.name,
                'type': machine.machine_type,
                'status': status,
                'current_operator': current_operator,
                'current_session': {
                    'start_time': ensure_timezone_aware(current_session.start_time) if current_session else None,
                    'parts_completed': current_log.run_completed_quantity if current_log else (current_session.total_parts if current_session else 0)
                } if current_session else None,
                'current_job': {
                    'drawing_number': current_log.drawing_rel.drawing_number if current_log and current_log.drawing_rel else 'N/A',
                    'sap_id': current_log.drawing_rel.sap_id if current_log and current_log.drawing_rel else 'N/A',
                    'product_name': current_log.end_product_sap_id_rel.name if current_log and current_log.end_product_sap_id_rel else 'Unknown'
                } if current_log else None,
                'setup_time': round((current_log.setup_end_time - current_log.setup_start_time).total_seconds() / 60, 2) if current_log and current_log.setup_start_time and current_log.setup_end_time else None,
                'run_time': round((current_log.last_cycle_end_time - current_log.first_cycle_start_time).total_seconds() / 60, 2) if current_log and current_log.first_cycle_start_time and current_log.last_cycle_end_time else None,
                'status_duration': status_duration,
                'last_updated': ensure_timezone_aware(datetime.now())
            }
            machine_data.append(machine_metrics)
            overall_oee += machine_oee
            if current_session:
                todays_production += current_session.total_parts
        if total_machines > 0:
            overall_oee = (overall_oee / total_machines) * 100
            quality_rate = sum(m['oee']['quality'] for m in machine_data) / total_machines
        return render_template('digital_twin.html',
            machine_data=machine_data,
            overall_oee=overall_oee,
            active_machines=active_machines,
            total_machines=total_machines,
            todays_production=todays_production,
            quality_rate=quality_rate,
            now=now
        )
    except Exception as e:
        app.logger.error(f"Error in digital twin dashboard: {str(e)}")
        app.logger.error(f"Stack trace: {traceback.format_exc()}")
        flash('Error loading digital twin dashboard', 'danger')
        return redirect(url_for('home'))

@app.route('/quality_dashboard', methods=['GET', 'POST'])
@login_required
def quality_dashboard():
    """Handle quality dashboard view"""
    if not current_user.is_quality:
        flash('Access denied. Quality access required.', 'danger')
        return redirect(url_for('login_general'))
    
    try:
        if request.method == 'POST':
            inspector_name = request.form.get('inspector_name')
            if inspector_name:
                session['inspector_name'] = inspector_name
                session['login_time'] = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                flash(f'Welcome {inspector_name}!', 'success')
                return redirect(url_for('quality_dashboard'))
        
        # Get pending quality checks
        pending_checks = []
        
        # Get logs needing FPI (first piece inspection)
        fpi_pending_logs = OperatorLog.query.join(
    MachineDrawing, OperatorLog.drawing_id == MachineDrawing.id
).join(
    EndProduct, MachineDrawing.sap_id == EndProduct.sap_id
).join(
    Machine, OperatorLog.machine_id == Machine.id
).join(
    OperatorSession, OperatorLog.operator_session_id == OperatorSession.id
).filter(
    OperatorLog.current_status.in_(['cycle_completed', 'cycle_completed_pending_fpi', 'fpi_failed_setup_pending']),
    OperatorLog.production_hold_fpi == True,
    EndProduct.is_first_piece_fpi_required == True
).order_by(OperatorLog.created_at.desc()).all()
        
        # Get logs needing LPI (last piece inspection)
        lpi_pending_logs = OperatorLog.query.join(
    MachineDrawing, OperatorLog.drawing_id == MachineDrawing.id
).join(
    EndProduct, MachineDrawing.sap_id == EndProduct.sap_id
).join(
    Machine, OperatorLog.machine_id == Machine.id
).join(
    OperatorSession, OperatorLog.operator_session_id == OperatorSession.id
).filter(
    OperatorLog.current_status.in_(['cycle_completed', 'lpi_pending', 'lpi_failed']),
    OperatorLog.run_completed_quantity >= OperatorLog.run_planned_quantity,
    EndProduct.is_last_piece_lpi_required == True,
    OperatorLog.lpi_status.in_(['pending', None])
).order_by(OperatorLog.created_at.desc()).all()
        
        # Process FPI pending logs
        for log in fpi_pending_logs:
            waiting_since = log.created_at
            if waiting_since.tzinfo is None:
                waiting_since = waiting_since.replace(tzinfo=timezone.utc)
            pending_checks.append({
                'drawing_number': log.drawing_rel.drawing_number,
                'batch_id': log.batch_id,
                'machine_name': log.operator_session.machine_rel.name if log.operator_session and log.operator_session.machine_rel else None,
                'check_type': 'fpi',
                'status': 'Pending FPI',
                'operator_name': log.operator_session.operator_name,
                'waiting_since': waiting_since.strftime('%Y-%m-%d %H:%M:%S'),
                'log_id': log.id,
                'completed_qty': log.run_completed_quantity,
                'planned_qty': log.run_planned_quantity
            })
        # Process LPI pending logs
        for log in lpi_pending_logs:
            waiting_since = log.created_at
            if waiting_since.tzinfo is None:
                waiting_since = waiting_since.replace(tzinfo=timezone.utc)
            pending_checks.append({
                'drawing_number': log.drawing_rel.drawing_number,
                'batch_id': log.batch_id,
                'machine_name': log.operator_session.machine_rel.name if log.operator_session and log.operator_session.machine_rel else None,
                'check_type': 'lpi',
                'status': 'Pending LPI',
                'operator_name': log.operator_session.operator_name,
                'waiting_since': waiting_since.strftime('%Y-%m-%d %H:%M:%S'),
                'log_id': log.id,
                'completed_qty': log.run_completed_quantity,
                'planned_qty': log.run_planned_quantity
            })
        
        # Get recent quality checks with proper timezone handling
        recent_checks = []
        quality_checks = QualityCheck.query.join(
            OperatorLog, QualityCheck.operator_log_id == OperatorLog.id
        ).join(
            MachineDrawing, OperatorLog.drawing_id == MachineDrawing.id
        ).order_by(QualityCheck.timestamp.desc()).limit(10).all()
        
        for check in quality_checks:
            check_time = check.timestamp
            if check_time.tzinfo is None:
                check_time = check_time.replace(tzinfo=timezone.utc)
                
            recent_checks.append({
                'drawing_number': check.operator_log_rel.drawing_rel.drawing_number,
                'type': check.check_type,
                'result': check.result,
                'inspector': check.inspector_name,
                'time': check_time.strftime('%Y-%m-%d %H:%M:%S'),
                'rejection_reason': check.rejection_reason if check.result in ['fail', 'rework'] else None,
                'quantities': {
                    'inspected': check.lpi_quantity_inspected if check.check_type == 'lpi' else None,
                    'rejected': check.lpi_quantity_rejected if check.check_type == 'lpi' else None,
                    'rework': check.lpi_quantity_to_rework if check.check_type == 'lpi' else None
                } if check.check_type == 'lpi' else None
            })
        
        return render_template('quality.html',
            pending_checks=pending_checks,
            quality_checks=recent_checks
        )
        
    except Exception as e:
        app.logger.error(f"Error in quality dashboard: {str(e)}")
        flash('Error loading quality dashboard', 'danger')
        return redirect(url_for('home'))

@app.route('/submit_quality_check', methods=['POST'])
@login_required
def submit_quality_check():
    """Handle quality check submission with batch-awareness and improved feedback"""
    try:
        if not session.get('inspector_name'):
            flash('Please set your inspector name first.', 'warning')
            return redirect(url_for('quality_dashboard'))
        
        drawing_number = request.form.get('drawing_number')
        check_type = request.form.get('check_type')
        result = request.form.get('result')
        rejection_reason = request.form.get('rejection_reason')
        batch_id = request.form.get('batch_id')
        batch_quantity = request.form.get('batch_quantity', type=int)
        
        # Batch-aware: Get the operator log for this drawing and batch
        operator_log = OperatorLog.query.join(
            MachineDrawing, OperatorLog.drawing_id == MachineDrawing.id
        ).filter(
            MachineDrawing.drawing_number == drawing_number,
            OperatorLog.batch_id == batch_id,
            OperatorLog.current_status.in_(['cycle_completed', 'cycle_completed_pending_fpi', 'fpi_failed_setup_pending', 'lpi_pending', 'lpi_failed', 'lpi_completed'])
        ).order_by(OperatorLog.created_at.desc()).first()
        
        if not operator_log:
            flash('No pending quality check found for this drawing and batch.', 'warning')
            return redirect(url_for('quality_dashboard'))
        
        now = datetime.now(timezone.utc)
        
        # Create quality check record (batch-aware)
        quality_check = QualityCheck(
            operator_log_id=operator_log.id,
            inspector_name=session['inspector_name'],
            check_type=check_type,
            result=result,
            rejection_reason=rejection_reason if result in ['fail', 'rework'] else None,
            timestamp=now,
            batch_id=batch_id
        )
        
        # Handle LPI quantities if provided
        if check_type == 'lpi':
            quality_check.lpi_quantity_inspected = request.form.get('lpi_quantity_inspected', type=int)
            quality_check.lpi_quantity_rejected = request.form.get('lpi_quantity_rejected', type=int)
            quality_check.lpi_quantity_to_rework = request.form.get('lpi_quantity_to_rework', type=int)
        
        db.session.add(quality_check)
        
        # Update operator log status and quantities based on result
        if check_type == 'fpi':
            operator_log.fpi_status = result
            operator_log.fpi_inspector = session['inspector_name']
            operator_log.fpi_timestamp = now
            if result == 'pass':
                operator_log.current_status = 'fpi_passed_ready_for_cycle'
                operator_log.production_hold_fpi = False
                flash('FPI passed. Operator can continue production.', 'success')
                socketio.emit('notify_operator', {'type': 'FPI_PASS', 'drawing': drawing_number, 'operator_log_id': operator_log.id})
            else:
                operator_log.current_status = 'fpi_failed_setup_pending'
                operator_log.production_hold_fpi = True
                flash('FPI failed or rework required. Operator is blocked until FPI is cleared.', 'danger')
                socketio.emit('notify_operator', {'type': 'FPI_FAIL', 'drawing': drawing_number, 'operator_log_id': operator_log.id, 'result': result})
                # Create rework record if needed
                if result == 'rework':
                    rework = ReworkQueue(
                        source_operator_log_id=operator_log.id,
                        drawing_id=operator_log.drawing_id,
                        originating_quality_check_id=quality_check.id,
                        quantity=1,  # FPI is always 1 piece
                        status='pending',
                        created_at=now
                        # batch_id removed, as ReworkQueue may not have it
                    )
                    db.session.add(rework)
                    operator_log.run_rework_quantity_fpi = (operator_log.run_rework_quantity_fpi or 0) + 1
                    socketio.emit('notify_manager', {'type': 'REWORK', 'drawing': drawing_number, 'operator_log_id': operator_log.id})
                # Create scrap record if needed
                elif result == 'fail':
                    scrap = ScrapLog(
                        operator_log_id=operator_log.id,
                        drawing_id=operator_log.drawing_id,
                        originating_quality_check_id=quality_check.id,
                        quantity_scrapped=1,  # FPI is always 1 piece
                        reason=rejection_reason,
                        scrapped_at=now
                        # batch_id removed, as ScrapLog does not have it
                    )
                    db.session.add(scrap)
                    operator_log.run_rejected_quantity_fpi = (operator_log.run_rejected_quantity_fpi or 0) + 1
        else:  # LPI
            operator_log.lpi_status = result
            operator_log.lpi_inspector = session['inspector_name']
            operator_log.lpi_timestamp = now
            if result == 'pass':
                operator_log.current_status = 'lpi_completed'
                operator_log.run_completed_quantity += (quality_check.lpi_quantity_inspected or 0)
                flash('LPI passed. Batch is ready for shipping.', 'success')
                socketio.emit('notify_operator', {'type': 'LPI_PASS', 'drawing': drawing_number, 'operator_log_id': operator_log.id})
            else:
                flash('LPI failed or rework required. Batch cannot be shipped until LPI is cleared.', 'danger')
                socketio.emit('notify_operator', {'type': 'LPI_FAIL', 'drawing': drawing_number, 'operator_log_id': operator_log.id, 'result': result})
                # Create rework record if needed
                if result == 'rework' and quality_check.lpi_quantity_to_rework:
                    rework = ReworkQueue(
                        source_operator_log_id=operator_log.id,
                        drawing_id=operator_log.drawing_id,
                        originating_quality_check_id=quality_check.id,
                        quantity=quality_check.lpi_quantity_to_rework,
                        status='pending',
                        created_at=now,
                        batch_id=batch_id
                    )
                    db.session.add(rework)
                    operator_log.run_rework_quantity_lpi = (operator_log.run_rework_quantity_lpi or 0) + (quality_check.lpi_quantity_to_rework or 0)
                    socketio.emit('notify_manager', {'type': 'REWORK', 'drawing': drawing_number, 'operator_log_id': operator_log.id})
                # Create scrap record if needed
                if quality_check.lpi_quantity_rejected:
                    scrap = ScrapLog(
                        operator_log_id=operator_log.id,
                        drawing_id=operator_log.drawing_id,
                        originating_quality_check_id=quality_check.id,
                        quantity_scrapped=quality_check.lpi_quantity_rejected,
                        reason=rejection_reason,
                        scrapped_at=now,
                        scrapped_by=session['inspector_name'],
                        batch_id=batch_id
                    )
                    db.session.add(scrap)
                    operator_log.run_rejected_quantity_lpi = (operator_log.run_rejected_quantity_lpi or 0) + (quality_check.lpi_quantity_rejected or 0)
        db.session.commit()
        flash('Quality check submitted successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error submitting quality check: {str(e)}")
        flash('Error submitting quality check.', 'danger')
    return redirect(url_for('quality_dashboard'))

def emit_digital_twin_update(machine=None):
    """Emit digital twin updates via WebSocket"""
    try:
        with app.app_context():
            if machine:
                # Single machine update
                current_session = OperatorSession.query.filter_by(
                    machine_id=machine.id,
                    end_time=None
                ).first()
                
                # Calculate OEE components
                availability = calculate_availability(machine)
                performance = calculate_performance(machine)
                quality = calculate_quality(machine)
                machine_oee = (availability * performance * quality) / 10000
                
                # Get current operator
                current_operator = None
                if current_session:
                    current_operator = {
                        'name': current_session.operator_name,
                        'shift': current_session.shift
                    }
                
                # Get current job
                current_job = None
                current_log = OperatorLog.query.filter_by(
                    operator_session_id=current_session.id if current_session else None,
                    current_status='cycle_started'
                ).order_by(OperatorLog.created_at.desc()).first()
                
                if current_log and current_log.drawing_rel:
                    current_job = {
                        'drawing_number': current_log.drawing_rel.drawing_number,
                        'sap_id': current_log.drawing_rel.sap_id,
                        'product_name': current_log.end_product_sap_id_rel.name if current_log.end_product_sap_id_rel else 'Unknown'
                    }
                
                # Determine machine status
                if current_session:
                    if current_log and current_log.current_status == 'cycle_started':
                        status = 'running'
                    elif current_log and current_log.current_status == 'setup_started':
                        status = 'setup'
                    else:
                        status = 'idle'
                else:
                    status = 'offline'
                
                # Emit machine update
                socketio.emit('machine_update', {
                    'machine_name': machine.name,
                    'status': status,
                    'status_text': status.title(),
                    'oee': {
                        'availability': availability,
                        'performance': performance,
                        'quality': quality,
                        'overall': machine_oee * 100
                    },
                    'current_operator': current_operator,
                    'current_session': {
                        'start_time': current_session.start_time.isoformat() if current_session else None,
                        'parts_completed': current_session.total_parts if current_session else 0
                    } if current_session else None,
                    'current_job': current_job
                })
            
            # Summary update
            machines = Machine.query.all()
            total_machines = len(machines)
            active_machines = sum(1 for m in machines if OperatorSession.query.filter_by(
                machine_id=m.id,
                end_time=None
            ).first() is not None)
            
            overall_oee = sum(
                (calculate_availability(m) * calculate_performance(m) * calculate_quality(m)) / 10000
                for m in machines
            ) / total_machines * 100 if total_machines > 0 else 0
            
            todays_production = sum(
                session.total_parts
                for m in machines
                for session in OperatorSession.query.filter_by(machine_id=m.id, end_time=None).all()
            )
            
            quality_rate = sum(calculate_quality(m) for m in machines) / total_machines if total_machines > 0 else 0
            
            socketio.emit('summary_update', {
                'overall_oee': overall_oee,
                'active_machines': active_machines,
                'total_machines': total_machines,
                'todays_production': todays_production,
                'quality_rate': quality_rate
            })
            
    except Exception as e:
        app.logger.error(f"Error emitting digital twin update: {str(e)}")
        app.logger.error(f"Stack trace: {traceback.format_exc()}")

# Add to existing routes where machine state changes
@app.route('/operator_panel/<machine_name>', methods=['GET', 'POST'])
@login_required
def operator_panel(machine_name):
    """Generic operator panel route for all machines"""
    try:
        machine = Machine.query.filter_by(name=machine_name).first_or_404()
        active_session = OperatorSession.query.filter_by(
            machine_id=machine.id,
            is_active=True
        ).first()
        available_assignments = MachineDrawingAssignment.query.filter(
            MachineDrawingAssignment.machine_id == machine.id
        ).all()
        assignment_rows = []
        for assignment in available_assignments:
            assignment_rows.append({
                'drawing_number': assignment.drawing_rel.drawing_number,
                'assigned_quantity': assignment.assigned_quantity,
                'completed_quantity': assignment.completed_quantity,
                'tool_number': assignment.tool_number,
                'status': assignment.status
            })
        # Get current operator log (all active statuses)
        current_log = OperatorLog.query.filter(
            OperatorLog.operator_session_id == active_session.id if active_session else None,
            OperatorLog.current_status.in_(['setup_started', 'setup_done', 'cycle_started', 'cycle_paused', 'fpi_passed_ready_for_cycle', 'cycle_completed', 'cycle_completed_pending_fpi', 'lpi_pending'])
        ).order_by(OperatorLog.created_at.desc()).first()
        if request.method == 'POST':
            action = request.form.get('action')
            drawing_number = request.form.get('drawing_number_input', '').strip()
            batch_id = request.form.get('batch_id')
            # Find active drawing and assignment
            active_drawing = None
            active_assignment = None
            for assignment in available_assignments:
                if assignment.drawing_rel.drawing_number.strip() == drawing_number:
                    active_drawing = assignment.drawing_rel
                    active_assignment = assignment
                    break
            # Find latest log for this drawing/batch
            batch_log = None
            if active_session and active_drawing:
                q = OperatorLog.query.filter(
                    OperatorLog.operator_session_id == active_session.id,
                    OperatorLog.drawing_id == active_drawing.id
                )
                if batch_id:
                    q = q.filter(OperatorLog.batch_id == batch_id)
                batch_log = q.order_by(OperatorLog.created_at.desc()).first()
            # --- Strict Enforcement ---
            if action == 'start_setup':
                # Block if an active log exists for this drawing/batch
                if batch_log and batch_log.current_status not in ['cycle_completed', 'lpi_completed', 'admin_closed']:
                    flash('Setup already started for this drawing/batch. Complete or close the current log first.', 'danger')
                    return redirect(request.url)
                if not active_drawing:
                    flash('Drawing not found or not assigned to this machine.', 'danger')
                    return redirect(request.url)
                if not active_session:
                    flash('No active operator session.', 'danger')
                    return redirect(request.url)
                if not batch_id:
                    qty = active_assignment.assigned_quantity if active_assignment else 1
                    new_batch = Batch(drawing_id=active_drawing.id, batch_quantity=qty)
                    db.session.add(new_batch)
                    db.session.commit()
                    batch_id = new_batch.id
                new_log = OperatorLog(
                    operator_session_id=active_session.id,
                    drawing_id=active_drawing.id,
                    machine_id=machine.id,
                    current_status='setup_started',
                    setup_start_time=datetime.now(timezone.utc),
                    batch_id=batch_id
                )
                db.session.add(new_log)
                db.session.commit()
                flash(f'Setup started for drawing {drawing_number}', 'success')
                return redirect(request.url)
            elif action == 'cycle_start':
                # Only block if FPI is pending after first piece (i.e., after first cycle_completed_pending_fpi)
                if current_log and current_log.current_status == 'cycle_completed_pending_fpi' and current_log.fpi_status not in ['pass', 'passed']:
                    flash('FPI must be passed before starting production cycle for this batch.', 'danger')
                    return redirect(request.url)
                if current_log and current_log.current_status in ['setup_done', 'fpi_passed_ready_for_cycle', 'cycle_paused']:
                    current_log.current_status = 'cycle_started'
                    current_log.cycle_start_time = datetime.now(timezone.utc)
                    db.session.commit()
                    flash('Cycle started.', 'success')
                return redirect(request.url)
            elif action == 'cycle_complete':
                if current_log and current_log.current_status == 'cycle_started':
                    is_first_piece = (current_log.run_completed_quantity == 0)
                    is_last_piece = (current_log.run_completed_quantity + 1 >= current_log.run_planned_quantity)
                    # Check if LPI is required for this product
                    lpi_required = False
                    if current_log.drawing_rel and current_log.drawing_rel.end_product_rel:
                        lpi_required = getattr(current_log.drawing_rel.end_product_rel, 'is_last_piece_lpi_required', False)
                    if not is_first_piece and current_log.fpi_status not in ['pass', 'passed']:
                        flash('FPI must be passed before completing cycle for this batch.', 'danger')
                        return redirect(request.url)
                    current_log.run_completed_quantity += 1
                    if is_first_piece:
                        current_log.current_status = 'cycle_completed_pending_fpi'
                        db.session.commit()
                        flash('First piece completed. Awaiting FPI (First Piece Inspection).', 'info')
                        return redirect(request.url)
                    elif is_last_piece and lpi_required:
                        current_log.current_status = 'lpi_pending'
                        db.session.commit()
                        flash('Last piece completed. Awaiting LPI (Last Piece Inspection).', 'info')
                        return redirect(request.url)
                    else:
                        current_log.current_status = 'cycle_completed'
                        db.session.commit()
                        flash('Cycle completed.', 'success')
                return redirect(request.url)
            elif action == 'ship_quantity':
                # Block if LPI required and not passed for this batch
                if batch_log and batch_log.lpi_status not in ['pass', 'passed']:
                    flash('LPI must be passed before shipping this batch.', 'danger')
                    return redirect(request.url)
                # ... existing ship logic ...
                # --- END OF POST LOGIC ---
            elif action == 'setup_done':
                if current_log and current_log.current_status == 'setup_started':
                    current_log.current_status = 'setup_done'
                    current_log.setup_end_time = datetime.now(timezone.utc)
                    db.session.commit()
                    flash('Setup marked as done.', 'success')
                else:
                    flash('No setup in progress to mark as done.', 'warning')
                return redirect(request.url)
            elif action == 'select_drawing_and_start_session':
                if not active_drawing:
                    session.pop('current_drawing_number', None)
                    flash('Drawing not found or not assigned to this machine.', 'danger')
                    return redirect(request.url)
                session['current_drawing_number'] = drawing_number
                session.modified = True
                flash(f'Drawing {drawing_number} selected.', 'success')
                return redirect(request.url)
        # For GET requests or after POST, always render the panel
        drawing_number = session.get('current_drawing_number')
        active_drawing = None
        active_assignment = None
        for assignment in available_assignments:
            if assignment.drawing_rel.drawing_number.strip() == (drawing_number or ''):
                active_drawing = assignment.drawing_rel
                active_assignment = assignment
                break
        # Calculate metrics for template
        parts_today = db.session.query(func.sum(OperatorLog.run_completed_quantity)).filter(
            OperatorLog.machine_id == machine.id,
            OperatorLog.created_at >= datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        ).scalar() or 0
        uptime = 0
        if active_session and active_session.login_time:
            session_start = ensure_timezone_aware(active_session.login_time)
            uptime = (datetime.now(timezone.utc) - session_start).total_seconds() / 3600.0
        assigned_qty = active_assignment.assigned_quantity if active_assignment else 0
        completed_qty = active_assignment.completed_quantity if active_assignment else 0
        passed_qty = 0
        fpi_status = 'pending'
        lpi_status = 'pending'
        if current_log:
            passed_qty = current_log.run_completed_quantity - (current_log.run_rejected_quantity_fpi or 0) - (current_log.run_rejected_quantity_lpi or 0)
            fpi_status = current_log.fpi_status or 'pending'
            lpi_status = current_log.lpi_status or 'pending'
        return render_template(
            'operator_panel.html',
            machine=machine,
            current_machine_obj=machine,
            active_drawing=active_drawing,
            assignment_rows=assignment_rows,
            current_log=current_log,
            parts_today=parts_today,
            uptime=uptime,
            assigned_qty=assigned_qty,
            completed_qty=completed_qty,
            passed_qty=passed_qty,
            fpi_status=fpi_status,
            lpi_status=lpi_status
        )

    except Exception as e:
        app.logger.error(f"Error in operator panel: {str(e)}")
        app.logger.error(f"Stack trace: {traceback.format_exc()}")
        flash('An error occurred. Please try again.', 'danger')
        return redirect(url_for('home'))

# ... existing code ...

# Initialize scheduler
scheduler = BackgroundScheduler()
scheduler.add_job(func=emit_digital_twin_update, trigger="interval", seconds=30)
scheduler.start()

# Shut down the scheduler when exiting the app
atexit.register(lambda: scheduler.shutdown())

@app.route('/set_quality_inspector', methods=['POST'])
@login_required
def set_quality_inspector():
    """Set quality inspector name in session"""
    if not current_user.is_quality:
        return jsonify({'error': 'Access denied'}), 403
        
    try:
        inspector_name = request.form.get('inspector_name')
        if not inspector_name:
            return jsonify({'error': 'Inspector name is required'}), 400
            
        session['quality_inspector_name'] = inspector_name
        session.modified = True
        
        return jsonify({
            'success': True,
            'inspector_name': inspector_name
        })
        
    except Exception as e:
        app.logger.error(f"Error setting quality inspector name: {str(e)}")
        return jsonify({'error': str(e)}), 500

@socketio.on('quality_status_change')
def handle_quality_status_change(data):
    """Handle quality status changes via WebSocket"""
    try:
        if not current_user.is_quality:
            raise ConnectionRefusedError('Access denied')
            
        log_id = data.get('log_id')
        status = data.get('status')
        check_type = data.get('check_type')
        inspector_name = session.get('quality_inspector_name')
        
        if not all([log_id, status, check_type, inspector_name]):
            raise ValueError('Missing required fields')
            
        with app.app_context():
            log = OperatorLog.query.get(log_id)
            if not log:
                raise ValueError('Log not found')
                
            # Update quality status
            if check_type == 'fpi':
                log.fpi_status = status
                log.fpi_inspector = inspector_name
                log.fpi_timestamp = datetime.now(timezone.utc)
            else:  # lpi
                log.lpi_status = status
                log.lpi_inspector = inspector_name
                log.lpi_timestamp = datetime.now(timezone.utc)
                
            # Create quality check record
            quality_check = QualityCheck(
                operator_log_id=log_id,
                inspector_name=inspector_name,
                check_type=check_type,
                result=status,
                timestamp=datetime.now(timezone.utc)
            )
            db.session.add(quality_check)
            db.session.commit()
            
            # Emit update to all clients
            emit('quality_update', {
                'log_id': log_id,
                'status': status,
                'check_type': check_type,
                'inspector_name': inspector_name,
                'timestamp': datetime.now(timezone.utc).isoformat()
            }, broadcast=True)
            
            # Update digital twin since quality affects OEE
            if log.operator_session_rel and log.operator_session_rel.machine_rel:
                emit_digital_twin_update(log.operator_session_rel.machine_rel)
                
    except Exception as e:
        app.logger.error(f"Error handling quality status change: {str(e)}")
        emit('error', {'message': str(e)})

# Register blueprints
app.register_blueprint(route_bp)
app.register_blueprint(operator_log_bp)

@app.route('/part-finder')
@login_required
def part_finder():
    return render_template('part_finder.html')

@app.route('/api/part-finder/search')
@login_required
def search_part():
    query = request.args.get('query', '').strip()
    if not query:
        return jsonify({'status': 'error', 'message': 'No search query provided'})
    try:
        # Robust search by SAP ID or Drawing Number (trimmed)
        print(f"Searching for: '{query.strip()}'")
        drawing = MachineDrawing.query.filter(
            or_(
                func.trim(MachineDrawing.drawing_number) == query.strip(),
                func.trim(MachineDrawing.sap_id) == query.strip()
            )
        ).first()
        if drawing:
            print(f"Found drawing: drawing_number={drawing.drawing_number}, sap_id={drawing.sap_id}")
        else:
            print("No drawing found")
        if not drawing:
            # Debug: print all MachineDrawing records
            all_drawings = MachineDrawing.query.all()
            print("All MachineDrawings in DB:")
            for d in all_drawings:
                print(f"drawing_number={d.drawing_number}, sap_id={d.sap_id}")
            return jsonify({'status': 'success', 'part': None})
        # Get all operator logs for this drawing
        operator_logs = OperatorLog.query.filter_by(drawing_id=drawing.id).all()
        # Group by batch_id
        batches = {}
        for log in operator_logs:
            if not log.batch_id:
                continue
            if log.batch_id not in batches:
                batches[log.batch_id] = {
                    'batch_id': log.batch_id,
                    'batch_quantity': log.batch_quantity,
                    'current_machine': log.machine_id,
                    'logs': [],
                    'qc': [],
                    'transfers': []
                }
            batches[log.batch_id]['logs'].append({
                'id': log.id,
                'timestamp': log.created_at,
                'machine_name': log.machine_id,
                'operation_type': log.current_status,
                'quantity': log.run_completed_quantity,
                'quality_status': log.quality_status,
                'inspector': log.lpi_inspector if log.lpi_inspector else log.fpi_inspector,
                'notes': log.notes
            })
        # Get all quality checks for this drawing
        quality_checks = QualityCheck.query.join(OperatorLog, QualityCheck.operator_log_id == OperatorLog.id).filter(
                OperatorLog.drawing_id == drawing.id
            ).all()
        for qc in quality_checks:
            if qc.batch_id and qc.batch_id in batches:
                batches[qc.batch_id]['qc'].append({
                    'id': qc.id,
                    'timestamp': qc.timestamp,
                    'check_type': qc.check_type,
                    'result': qc.result,
                    'inspector_name': qc.inspector_name,
                    'quantity': qc.batch_quantity,
                    'rejection_reason': qc.rejection_reason
                })
        # Get all shipping records for this drawing
        shipping_records = ShippingRecord.query.filter_by(drawing_number=drawing.drawing_number).all()
        for record in shipping_records:
            for batch in batches.values():
                if batch['current_machine'] == record.from_machine:
                    batch['transfers'].append({
                        'from': record.from_machine,
                        'to': record.to_machine,
                        'quantity': record.quantity,
                        'date': record.date
                    })
        # Always include all route steps as batches/checkpoints
        route = ProcessRoute.query.filter_by(sap_id=drawing.sap_id).order_by(ProcessRoute.created_at.desc()).first()
        batch_summary = []
        if route:
            operations = RouteOperation.query.filter_by(route_id=route.id).order_by(RouteOperation.sequence).all()
            for op in operations:
                # Find if any batch/log exists for this operation
                found = False
                for batch in batches.values():
                    # If any log in this batch matches this operation type, include the batch
                    if any(l['operation_type'] == op.operation_type for l in batch['logs']):
                        batch_summary.append({
                            'batch_id': batch['batch_id'],
                            'batch_quantity': batch['batch_quantity'],
                            'current_machine': op.assigned_machine,
                            'operation_type': op.operation_type,
                            'fpi_status': next((qc['result'] for qc in batch['qc'] if qc['check_type'].lower() == 'fpi'), 'Pending'),
                            'lpi_status': next((qc['result'] for qc in batch['qc'] if qc['check_type'].lower() == 'lpi'), 'Pending'),
                            'logs': batch['logs'],
                            'qc': batch['qc'],
                            'transfers': batch['transfers']
                        })
                        found = True
                        break
                if not found:
                    # No log/batch for this operation, add a pending checkpoint
                    batch_summary.append({
                        'batch_id': f'pending-{op.id}',
                        'batch_quantity': 0,
                        'current_machine': op.assigned_machine,
                        'operation_type': op.operation_type,
                        'fpi_status': 'Pending',
                        'lpi_status': 'Pending',
                        'logs': [],
                        'qc': [],
                        'transfers': []
                    })
        # Prepare response
        part_details = {
            'sap_id': drawing.sap_id,
            'drawing_number': drawing.drawing_number,
            'batches': batch_summary
        }
        return jsonify({'status': 'success', 'part': part_details})
    except Exception as e:
        app.logger.error(f'Error in part finder search: {str(e)}')
        return jsonify({'status': 'error', 'message': str(e)})

def determine_next_operation_and_machines(machine_name, current_operation, shipped_quantity):
    """Stub for next operation/machine determination. Replace with actual logic as needed."""
    # TODO: Implement actual logic for determining next operation and machines
    return None, []

def generate_batch_id(*args, **kwargs):
    raise NotImplementedError('Batch IDs are now auto-increment integers. This function should not be used.')

@app.route('/batch/history', methods=['GET'])
@login_required
def batch_history():
    batch_id = request.args.get('batch_id')
    drawing_number = request.args.get('drawing_number')
    query = OperatorLog.query
    if batch_id:
        query = query.filter(OperatorLog.batch_id == int(batch_id))
    if drawing_number:
        query = query.join(MachineDrawing, OperatorLog.drawing_id == MachineDrawing.id).filter(MachineDrawing.drawing_number == drawing_number)
    logs = query.order_by(OperatorLog.created_at.desc()).all()
    history = []
    for log in logs:
        history.append({
            'batch_id': log.batch_id,
            'drawing_number': log.drawing_rel.drawing_number if log.drawing_rel else '',
            'machine': log.machine_id,
            'status': log.current_status,
            'setup_start_time': log.setup_start_time.strftime('%Y-%m-%d %H:%M:%S') if log.setup_start_time else '',
            'setup_end_time': log.setup_end_time.strftime('%Y-%m-%d %H:%M:%S') if log.setup_end_time else '',
            'fpi_status': log.fpi_status,
            'fpi_timestamp': log.fpi_timestamp.strftime('%Y-%m-%d %H:%M:%S') if log.fpi_timestamp else '',
            'lpi_status': log.lpi_status,
            'lpi_timestamp': log.lpi_timestamp.strftime('%Y-%m-%d %H:%M:%S') if log.lpi_timestamp else '',
            'run_planned_quantity': log.run_planned_quantity,
            'run_completed_quantity': log.run_completed_quantity,
            'run_rejected_quantity_fpi': log.run_rejected_quantity_fpi,
            'run_rejected_quantity_lpi': log.run_rejected_quantity_lpi,
            'run_rework_quantity_fpi': log.run_rework_quantity_fpi,
            'run_rework_quantity_lpi': log.run_rework_quantity_lpi,
            'created_at': log.created_at.strftime('%Y-%m-%d %H:%M:%S') if log.created_at else '',
        })
    return jsonify({'history': history})

def update_current_operation_and_create_next_operation(machine_name, current_operation, shipped_quantity, next_operation, next_machines):
    # TODO: Implement operation routing logic
    pass

@app.route('/api/assign_route', methods=['POST'])
@login_required
def api_assign_route():
    """Assign route operations to machines with per-operation tool assignment"""
    if not (current_user.is_plant_head or current_user.is_manager):
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    try:
        data = request.get_json()
        sap_id = data.get('sap_id')
        quantity = int(data.get('quantity', 0))
        operations = data.get('operations', [])
        if not sap_id or not quantity or not operations:
            return jsonify({'success': False, 'error': 'Missing required fields'}), 400
        # Get or create drawing
        drawing = MachineDrawing.query.filter(
            db.or_(func.trim(MachineDrawing.drawing_number) == sap_id.strip(), func.trim(MachineDrawing.sap_id) == sap_id.strip())
        ).first()
        if not drawing:
            drawing = MachineDrawing(
                drawing_number=sap_id.strip(),
                sap_id=sap_id.strip()
            )
            db.session.add(drawing)
            db.session.flush()
        # Assign each operation
        for op in operations:
            machine_name = op.get('assigned_machine')
            tool_number = op.get('tool_number', '')
            if not machine_name:
                continue  # skip if no machine assigned
            machine = Machine.query.filter_by(name=machine_name).first()
            if not machine:
                continue  # skip if machine not found
            # Validate tool_number (optional, but if present must be valid)
            if tool_number:
                tools = tool_number.split(',')
                import re
                valid = all(tool and re.match(r'^[A-Z0-9\-_.#@/]+$', tool) for tool in tools)
                if not valid:
                    return jsonify({'success': False, 'error': f'Invalid tool format for {machine_name}: Use only uppercase, numbers, and -_.#@/ (no spaces, no empty names)'}), 400
            assignment = MachineDrawingAssignment(
                drawing_id=drawing.id,
                machine_id=machine.id,
                assigned_quantity=quantity,
                tool_number=tool_number,
                status='assigned',
                assigned_by=current_user.id,
                assigned_at=datetime.now(timezone.utc)
            )
            db.session.add(assignment)
        db.session.commit()
        return jsonify({'success': True}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/debug/operator_log_schema')
def debug_operator_log_schema():
    from sqlalchemy import inspect
    inspector = inspect(db.engine)
    columns = inspector.get_columns('operator_log')
    return '<br>'.join(f"{col['name']} ({col['type']})" for col in columns)
#last line 4410