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
import pandas as pd
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
    MachineDrawingAssignment, TransferHistory
)

# Create tables
with app.app_context():
    db.create_all()
    app.logger.info('Database tables created')
    # Add after app initialization, before routes
UPDATE_INTERVAL = 30  # seconds between updates
ERROR_RETRY_INTERVAL = 60  # seconds to wait after error

# Import and register blueprints
from live_routes import live_bp
app.register_blueprint(live_bp)

# Initialize Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login_general'
login_manager.login_message = 'Please log in to access this page.'
login_manager.login_message_category = 'error'

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
        return [('Leadwell-1', 'Leadwell-1'), ('Leadwell-2', 'Leadwell-2')]  # Fallback to default machines

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
            ('Leadwell-1', 'available'),
            ('Leadwell-2', 'available')
        ]
        for name, status in machines:
            try:
                machine = Machine(name=name, status=status)
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
    """Ensure that required machines exist in the database"""
    try:
        # Check if machines exist
        machines = Machine.query.all()
        existing_names = {m.name for m in machines}
        
        # Required machine names
        required_machines = ['Leadwell-1', 'Leadwell-2']
        
        # Add any missing machines
        for name in required_machines:
            if name not in existing_names:
                new_machine = Machine(name=name, status='available')
                db.session.add(new_machine)
                app.logger.info(f"Added missing machine: {name}")
        
        db.session.commit()
        return True
    except Exception as e:
        app.logger.error(f"Error ensuring machines exist: {str(e)}")
        db.session.rollback()
        return False

def initialize_application():
    """Initialize application state before first request"""
    try:
        with app.app_context():
            # Create database if it doesn't exist
            db.create_all()
            app.logger.info("Database tables created")
            
            # Ensure machines exist with correct names
            machines = Machine.query.all()
            existing_names = {m.name for m in machines}
            
            # Required machine names - MUST match operator routes
            required_machines = ['Leadwell-1', 'Leadwell-2']
            
            # First fix any incorrect machine names
            for machine in machines:
                if machine.name == 'Leadwell1':
                    machine.name = 'Leadwell-1'
                    app.logger.info("Fixed machine name from Leadwell1 to Leadwell-1")
                elif machine.name == 'Leadwell2':
                    machine.name = 'Leadwell-2'
                    app.logger.info("Fixed machine name from Leadwell2 to Leadwell-2")
            
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
            
            # Then add only truly missing machines
            existing_names = {m.name for m in Machine.query.all()}  # Refresh after fixes
            for name in required_machines:
                if name not in existing_names:
                    try:
                        new_machine = Machine(name=name, status='available')
                        db.session.add(new_machine)
                        db.session.commit()
                        app.logger.info(f"Added missing machine: {name}")
                    except IntegrityError:
                        db.session.rollback()
                        app.logger.info(f"Machine {name} already exists")
            
            # Ensure admin user exists
            admin_user = User.query.filter_by(username='admin').first()
            if not admin_user:
                admin_user = User(
                    username='admin',
                    password=generate_password_hash('admin123'),
                    role='admin',
                    is_active=True
                )
                db.session.add(admin_user)
                db.session.commit()
            
            app.logger.info("Application initialized successfully")
            return True
    except Exception as e:
        app.logger.error(f"Error initializing application: {str(e)}")
        if 'db' in locals():
            db.session.rollback()
        return False

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
            setup_database()
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

# --- OPERATOR ---
@app.route('/operator/leadwell-1', methods=['GET', 'POST'])
def operator_panel_leadwell1():
    if session.get('active_role') != 'operator' or session.get('machine_name') != 'Leadwell-1':
        flash('Access denied. Please login as operator for Leadwell-1', 'danger')
        return redirect(url_for('operator_login'))
    if request.method == 'POST' and request.form.get('action') == 'select_drawing_and_start_session':
        drawing_number = request.form.get('drawing_number_input', '').strip()
        if drawing_number:
            session['current_drawing_number'] = drawing_number
            session.modified = True
    return operator_panel_common('Leadwell-1', 'operator_leadwell1.html')

@app.route('/operator/leadwell-2', methods=['GET', 'POST'])
def operator_panel_leadwell2():
    if session.get('active_role') != 'operator' or session.get('machine_name') != 'Leadwell-2':
        flash('Access denied. Please login as operator for Leadwell-2', 'danger')
        return redirect(url_for('operator_login'))
    if request.method == 'POST' and request.form.get('action') == 'select_drawing_and_start_session':
        drawing_number = request.form.get('drawing_number_input', '').strip()
        if drawing_number:
            session['current_drawing_number'] = drawing_number
            session.modified = True
    return operator_panel_common('Leadwell-2', 'operator_leadwell2.html')

def get_redirect_url(machine_name):
    """Get the correct redirect URL for a machine"""
    machine_routes = {
        'Leadwell-1': 'operator_panel_leadwell1',
        'Leadwell-2': 'operator_panel_leadwell2'
    }
    if machine_name not in machine_routes:
        flash('Currently only Leadwell-1 and Leadwell-2 machines are supported.', 'warning')
        return url_for('operator_login')
    return url_for(machine_routes[machine_name])

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
        available_assignments = MachineDrawingAssignment.query.filter_by(
            machine_id=machine.id,
            status='assigned'
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
                # Only start if no active log for this drawing
                if not current_log or current_log.drawing_id != drawing.id or current_log.current_status in ['pending_setup', 'lpi_completed', 'admin_closed', 'fpi_failed_setup_pending']:
                    new_log = OperatorLog(
                        operator_session_id=active_session.id,
                        drawing_id=drawing.id,
                        current_status='setup_started',
                        run_planned_quantity=0,
                        run_completed_quantity=0
                    )
                    db.session.add(new_log)
                    db.session.commit()
                    flash(f'Setup started for drawing {form_drawing_number}', 'success')
                    print(f'Setup started for drawing {form_drawing_number}')
                else:
                    flash('Active log already exists for this drawing.', 'warning')
                    print('Active log already exists for this drawing.')
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
            elif action == 'cycle_start' and current_log and current_log.current_status in ['setup_done', 'fpi_passed_ready_for_cycle', 'cycle_paused']:
                current_log.current_status = 'cycle_started'
                db.session.commit()
                flash('Cycle started.', 'success')
                print('Cycle started.')
                return redirect(request.url)
            elif action == 'cycle_complete' and current_log and current_log.current_status == 'cycle_started':
                # --- FPI/LPI ENFORCEMENT LOGIC ---
                assignment = MachineDrawingAssignment.query.filter_by(
                    machine_id=machine.id, drawing_id=current_log.drawing_id, status='assigned').first()
                completed_count = db.session.query(db.func.sum(OperatorLog.run_completed_quantity)).filter(
                    OperatorLog.machine_id == machine.id,
                    OperatorLog.drawing_id == current_log.drawing_id,
                    OperatorLog.quality_status == 'approved'
                ).scalar() or 0
                planned = assignment.assigned_quantity if assignment else 0
                # FPI: Only for first piece, repeat until passed
                if completed_count == 0 or (current_log.fpi_status in ['fail', 'rework', None, 'Pending'] and current_log.current_status == 'cycle_completed_pending_fpi'):
                    current_log.current_status = 'cycle_completed_pending_fpi'
                    db.session.commit()
                    flash('Cycle completed. Awaiting FPI (first piece inspection).', 'info')
                    return redirect(request.url)
                # LPI: Only for 4th or last piece, repeat until passed
                elif (completed_count+1 == 4 or completed_count+1 == planned) or (current_log.lpi_status in ['fail', 'rework', None] and current_log.current_status == 'cycle_completed_pending_lpi'):
                    current_log.current_status = 'cycle_completed_pending_lpi'
                    db.session.commit()
                    flash('Cycle completed. Awaiting LPI (last piece inspection).', 'info')
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
            lpi_status=lpi_status
        )

    except Exception as e:
        app.logger.error(f"Error in operator panel: {str(e)}")
        flash('Error loading operator panel', 'danger')
        return redirect(url_for('home'))

@app.route('/operator_logout', methods=['POST'])
def operator_logout():
    # The OperatorSession DB update is still important
    operator_session_id = session.get('operator_session_id')
    if operator_session_id:
        op_session_db = db.session.get(OperatorSession, operator_session_id) # Corrected from query.get
        if op_session_db and op_session_db.is_active:
            op_session_db.is_active = False
            op_session_db.logout_time = datetime.now(timezone.utc)
            db.session.commit()

    clear_user_session() # Unified session clearing

    flash('You have been logged out from Operator station.', 'info')
    return redirect(url_for('operator_login'))

@app.route('/operator_login', methods=['GET', 'POST'])
def operator_login():
    """Handle operator login with proper machine validation"""
    if request.method == 'POST':
        clear_user_session()
        
        operator_name = request.form.get('operator_name', '').strip()
        machine_name = request.form.get('machine_name')
        shift = request.form.get('shift')
        
        if not operator_name or not machine_name or not shift:
            flash('All fields are required for login.', 'danger')
            return redirect(url_for('operator_login'))
        
        try:
            # First ensure machines exist
            ensure_machines_exist()
            
            # Then verify machine exists
            machine = Machine.query.filter_by(name=machine_name).first()
            if not machine:
                flash(f'Machine {machine_name} not found.', 'danger')
                return redirect(url_for('operator_login'))
            
            # End any previous active sessions
            old_sessions = OperatorSession.query.filter(
                db.or_(
                    OperatorSession.operator_name == operator_name,
                    OperatorSession.machine_id == machine.id
                ),
                OperatorSession.is_active == True
            ).all()
            
            for old_session in old_sessions:
                old_session.is_active = False
                old_session.logout_time = datetime.now(timezone.utc)
                
                # Close any hanging logs
                hanging_logs = OperatorLog.query.filter(
                    OperatorLog.operator_session_id == old_session.id,
                    OperatorLog.current_status.notin_(['lpi_completed', 'admin_closed'])
                ).all()
                
                for log in hanging_logs:
                    log.current_status = 'admin_closed'
                    log.notes = (log.notes or "") + f"\nLog auto-closed due to new operator login at {datetime.now(timezone.utc)}."
            
            db.session.commit()
            
            # Create new session
            new_op_session = OperatorSession(
                operator_name=operator_name,
                machine_id=machine.id,
                shift=shift,
                is_active=True
            )
            db.session.add(new_op_session)
            db.session.commit()
            
            # Set session data
            session['active_role'] = 'operator'
            session['operator_session_id'] = new_op_session.id
            session['operator_name'] = operator_name
            session['machine_name'] = machine_name
            session['shift'] = shift
            
            # Create user object and login
            user = SimpleUser(f"{operator_name}|operator", 'operator')
            login_user(user)
            
            # Restore previous session state
            previous_state = restore_operator_session(operator_name, machine_name)
            if previous_state:
                session['current_drawing_id'] = previous_state['drawing_id']
                flash(f'Previous session state restored. Last drawing had {previous_state["completed_quantity"]}/{previous_state["planned_quantity"]} parts completed.', 'info')
            
            flash(f'Welcome {operator_name}! Logged in on {machine_name}, Shift {shift}.', 'success')
            return redirect(get_redirect_url(machine_name))
            
        except Exception as e:
            db.session.rollback()
            error_msg = f"Error during operator login: {str(e)}"
            app.logger.error(error_msg)
            flash('An error occurred during login. Please try again.', 'danger')
            return redirect(url_for('operator_login'))
    
    # GET request - show login form
    try:
        # First ensure machines exist
        ensure_machines_exist()
        return render_template('operator_login.html', machine_choices=get_machine_choices())
    except Exception as e:
        app.logger.error(f"Error loading operator login page: {str(e)}")
        flash('Error loading login page. Please try again.', 'danger')
        return redirect(url_for('home'))

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
    """Handle Socket.IO connection with rate limiting and validation"""
    try:
        if not current_user.is_authenticated:
            return False
            
        if not can_add_user():
            return False
            
        if not socket_rate_limiter.is_allowed(request.sid):
            current_app.logger.warning(f"Rate limit exceeded for client {request.sid}")
            return False
            
        add_user_session(request.sid, current_user.get_id())
        return True
    except Exception as e:
        current_app.logger.error(f"Socket.IO connection error: {str(e)}")
        return False

@socketio.on('disconnect')
def handle_disconnect():
    """Handle Socket.IO disconnection"""
    try:
        remove_user_session(request.sid)
    except Exception as e:
        current_app.logger.error(f"Socket.IO disconnection error: {str(e)}")

@socketio.on('request_update')
def handle_update_request():
    """Handle real-time update requests from clients"""
    try:
        # Get all machines
        machines = Machine.query.all()
        if not machines:
            return

        # Initialize variables for summary metrics
        total_machines = len(machines)
        active_machines = 0
        total_oee = 0
        machines_with_oee = 0
        today = datetime.now(timezone.utc).date()

        machine_data = []
        for machine in machines:
            try:
                metrics = calculate_machine_utilization(machine)
                if metrics['oee'] > 0:
                    total_oee += metrics['oee']
                    machines_with_oee += 1

                if machine.status in ['running', 'in_use', 'cycle_started']:
                    active_machines += 1

                # Get current operator log
                current_log = OperatorLog.query.filter(
                    OperatorLog.machine_id == machine.id,
                    OperatorLog.current_status.notin_(['cycle_completed', 'admin_closed'])
                ).order_by(OperatorLog.created_at.desc()).first()

                machine_data.append({
                    'name': machine.name,
                    'status': machine.status,
                    'oee': {
                        'availability': metrics['availability'] * 100,
                        'performance': metrics['performance'] * 100,
                        'quality': metrics['quality'] * 100,
                        'overall': metrics['oee'] * 100
                    },
                    'parts_completed': current_log.run_completed_quantity if current_log else 0
                })
            except Exception as machine_error:
                app.logger.error(f"Error processing machine update {machine.name}: {str(machine_error)}")
                continue

        # Calculate overall metrics
        overall_oee = (total_oee / machines_with_oee * 100) if machines_with_oee > 0 else 0

        # Calculate today's production
        todays_production = db.session.query(func.sum(OperatorLog.run_completed_quantity))\
            .filter(func.date(OperatorLog.created_at) == today)\
            .scalar() or 0

        # Calculate quality rate
        total_parts = db.session.query(
            func.sum(OperatorLog.run_completed_quantity)
        ).filter(
            func.date(OperatorLog.created_at) == today
        ).scalar() or 0

        rejected_parts = db.session.query(
            func.sum(OperatorLog.run_rejected_quantity_fpi + OperatorLog.run_rejected_quantity_lpi)
        ).filter(
            func.date(OperatorLog.created_at) == today
        ).scalar() or 0

        quality_rate = ((total_parts - rejected_parts) / total_parts * 100) if total_parts > 0 else 0

        # Emit update to client
        emit('machine_update', {
            'machines': machine_data,
            'overall_oee': overall_oee,
            'active_machines': active_machines,
            'total_machines': total_machines,
            'todays_production': todays_production,
            'quality_rate': quality_rate
        })

    except Exception as e:
        app.logger.error(f"Error handling update request: {str(e)}")
        app.logger.error(f"Stack trace: {traceback.format_exc()}")

def calculate_dpr_metrics(log):
    """Calculate DPR metrics including OEE components and timing"""
    try:
        # Setup Time calculation
        setup_time = 0
        if log and log.setup_start_time and log.setup_end_time:
            setup_time = (log.setup_end_time - log.setup_start_time).total_seconds() / 60

        # Cycle Time calculation
        cycle_time = 0
        total_cycle_time = 0
        if log and log.first_cycle_start_time and log.last_cycle_end_time and log.run_completed_quantity:
            total_cycle_time = (log.last_cycle_end_time - log.first_cycle_start_time).total_seconds() / 60
            cycle_time = total_cycle_time / log.run_completed_quantity if log.run_completed_quantity > 0 else 0

        # Get standard times from planner's sheet
        std_setup_time = 0
        std_cycle_time = 0
        if log and log.drawing_rel and log.drawing_rel.end_product_rel:
            std_setup_time = log.drawing_rel.end_product_rel.setup_time_std or 0
            std_cycle_time = log.drawing_rel.end_product_rel.cycle_time_std or 0

        # Calculate OEE components
        # Availability
        planned_production_time = 8 * 60  # 8 hours in minutes
        operating_time = planned_production_time - setup_time
        availability = (operating_time / planned_production_time) * 100 if planned_production_time > 0 else 0

        # Performance
        ideal_cycle_time = std_cycle_time
        actual_cycle_time = cycle_time
        performance = (ideal_cycle_time / actual_cycle_time) * 100 if actual_cycle_time > 0 and ideal_cycle_time > 0 else 0

        # Quality
        if log:
            total_parts = (log.run_completed_quantity or 0) + (log.run_rejected_quantity_fpi or 0) + \
                         (log.run_rejected_quantity_lpi or 0) + (log.run_rework_quantity_fpi or 0) + \
                         (log.run_rework_quantity_lpi or 0)
            good_parts = log.run_completed_quantity or 0
        else:
            total_parts = 0
            good_parts = 0
            
        quality = (good_parts / total_parts) * 100 if total_parts > 0 else 0

        # Overall OEE
        oee = (availability * performance * quality) / 10000

        return {
            'std_setup_time': std_setup_time,
            'actual_setup_time': setup_time,
            'std_cycle_time': std_cycle_time,
            'actual_cycle_time': cycle_time,
            'total_cycle_time': total_cycle_time,
            'availability': availability,
            'performance': performance,
            'quality': quality,
            'oee': oee
        }
    except Exception as e:
        app.logger.error(f"Error calculating DPR metrics: {str(e)}")
        return {
            'std_setup_time': 0, 'actual_setup_time': 0,
            'std_cycle_time': 0, 'actual_cycle_time': 0,
            'total_cycle_time': 0, 'availability': 0,
            'performance': 0, 'quality': 0, 'oee': 0
        }

@with_db_session
def emit_live_dpr_update():
    """Emit real-time DPR data updates with safe calculations"""
    try:
        app.logger.info("Starting DPR data emission...")
        
        # Get current production data with all required fields
        dpr_data = db.session.query(
            EndProduct.name,
            EndProduct.sap_id,
            EndProduct.quantity,
            Project.project_code,
            MachineDrawing.drawing_number,
            Machine.name.label('machine_name'),
            OperatorLog.setup_time,
            OperatorLog.current_status,
            OperatorLog.run_completed_quantity,
            OperatorLog.run_rejected_quantity_fpi,
            OperatorLog.run_rejected_quantity_lpi,
            OperatorLog.run_rework_quantity_fpi,
            OperatorLog.run_rework_quantity_lpi,
            OperatorLog.created_at,
            OperatorLog.setup_start_time,
            OperatorLog.setup_end_time,
            OperatorLog.first_cycle_start_time,
            OperatorLog.last_cycle_end_time,
            OperatorLog.id.label('operator_log_id'),
            OperatorLog.fpi_status,
            OperatorLog.lpi_status
        ).join(
            Project, EndProduct.project_id == Project.id
        ).outerjoin(
            MachineDrawing, MachineDrawing.sap_id == EndProduct.sap_id
        ).outerjoin(
            OperatorLog, OperatorLog.end_product_sap_id == EndProduct.sap_id
        ).outerjoin(
            Machine, Machine.id == OperatorLog.machine_id
        ).all()

        app.logger.info(f"Retrieved {len(dpr_data)} DPR records")

        # Format data for DPR
        formatted_data = []
        for row in dpr_data:
            try:
                operator_log = None
                if row.operator_log_id:
                    operator_log = OperatorLog.query.options(
                        db.joinedload(OperatorLog.drawing_rel).joinedload(MachineDrawing.end_product_rel)
                    ).get(row.operator_log_id)
                metrics = calculate_dpr_metrics(operator_log) if operator_log else {
                    'std_setup_time': 0, 'actual_setup_time': 0,
                    'std_cycle_time': 0, 'actual_cycle_time': 0,
                    'total_cycle_time': 0, 'availability': 0,
                    'performance': 0, 'quality': 0, 'oee': 0
                }
                created_at_ist = utc_to_ist(row.created_at)
                setup_start_ist = utc_to_ist(row.setup_start_time)
                setup_end_ist = utc_to_ist(row.setup_end_time)
                first_cycle_start_ist = utc_to_ist(row.first_cycle_start_time)
                last_cycle_end_ist = utc_to_ist(row.last_cycle_end_time)
                
                formatted_data.append({
                    'id': row.id,
                    'drawing_number': row.drawing_number,
                    'machine_name': row.machine_name,
                    'operator_name': row.operator_name,
                    'target': row.quantity,
                    'completed': row.run_completed_quantity,
                    'rejected': (row.run_rejected_quantity_fpi or 0) + (row.run_rejected_quantity_lpi or 0),
                    'rework': (row.run_rework_quantity_fpi or 0) + (row.run_rework_quantity_lpi or 0),
                    'std_setup_time': metrics['std_setup_time'],
                    'actual_setup_time': metrics['actual_setup_time'],
                    'std_cycle_time': metrics['std_cycle_time'],
                    'actual_cycle_time': metrics['actual_cycle_time'],
                    'total_cycle_time': metrics['total_cycle_time'],
                    'availability': metrics['availability'],
                    'performance': metrics['performance'],
                    'quality': metrics['quality'],
                    'oee': metrics['oee'],
                    'fpi_status': row.fpi_status,
                    'lpi_status': row.lpi_status,
                    'setup_start_ist': setup_start_ist.strftime('%Y-%m-%d %H:%M') if setup_start_ist else None,
                    'setup_end_ist': setup_end_ist.strftime('%Y-%m-%d %H:%M') if setup_end_ist else None,
                    'first_cycle_start_ist': first_cycle_start_ist.strftime('%Y-%m-%d %H:%M') if first_cycle_start_ist else None,
                    'last_cycle_end_ist': last_cycle_end_ist.strftime('%Y-%m-%d %H:%M') if last_cycle_end_ist else None
                })
            except Exception as row_error:
                app.logger.error(f"Error processing DPR row: {str(row_error)}")
                app.logger.error(f"Row data: {row}")
                continue

        app.logger.info(f"Formatted {len(formatted_data)} DPR records")
        socketio.emit('dpr_update', {'data': formatted_data})
        app.logger.info("DPR data emitted successfully")
    except Exception as e:
        current_app.logger.error(f"Error emitting DPR update: {str(e)}")

@with_db_session
def emit_machine_update():
    """Emit real-time machine status updates"""
    try:
        machines = Machine.query.all()
        machine_data = []
        
        for machine in machines:
            # Get active operator session
            active_session = OperatorSession.query.filter_by(
                machine_id=machine.id,
                is_active=True
            ).first()

            # Get current operator log
            current_log = None
            if active_session:
                current_log = OperatorLog.query.filter(
                    OperatorLog.operator_session_id == active_session.id,
                    OperatorLog.current_status.notin_(['cycle_completed', 'admin_closed'])
                ).order_by(OperatorLog.created_at.desc()).first()

            # Calculate metrics
            metrics = calculate_machine_utilization(machine)
            
            machine_data.append({
                'machine_name': machine.name,
                'status': machine.status,
                'operator': active_session.operator_name if active_session else None,
                'current_job': current_log.drawing_rel.drawing_number if current_log and current_log.drawing_rel else None,
                'parts_completed': current_log.run_completed_quantity if current_log else 0,
                'parts_rejected': (current_log.run_rejected_quantity_fpi + current_log.run_rejected_quantity_lpi) if current_log else 0,
                'metrics': {
                    'availability': metrics['availability'],
                    'performance': metrics['performance'],
                    'quality': metrics['quality'],
                    'oee': metrics['oee'] * 100  # Convert to percentage
                },
                'timestamp': datetime.now(timezone.utc).isoformat()
            })

        socketio.emit('machine_update', {'data': machine_data})
        
    except Exception as e:
        app.logger.error(f"Error emitting machine update: {str(e)}")

@with_db_session
def emit_quality_update():
    """Emit real-time quality metrics updates"""
    try:
        quality_data = {
            'last_update': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'metrics': {}
        }
        socketio.emit('quality_update', quality_data)
    except Exception as e:
        current_app.logger.error(f"Error emitting quality update: {str(e)}")

@with_db_session
def emit_production_update():
    """Emit real-time production metrics updates including end product progress"""
    try:
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

        # Get current production data
        production_data = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'metrics': {},
            'end_products': projects_data
        }
        socketio.emit('production_update', production_data)
    except Exception as e:
        current_app.logger.error(f"Error emitting production update: {str(e)}")

def schedule_updates():
    """Schedule periodic data updates with rate limiting"""
    update_limiter = RateLimiter(max_requests=2, time_window=30)  # Max 2 updates per 30 seconds
    
    while True:
        try:
            if update_limiter.is_allowed('scheduler'):
                with app.app_context():
                    app.logger.info("Starting scheduled updates...")
                    emit_live_dpr_update()
                    emit_machine_update()
                    emit_production_update()
                    emit_quality_update()
                    app.logger.info("Scheduled updates completed successfully")
        except Exception as e:
            app.logger.error(f"Error in update scheduler: {str(e)}")
            app.logger.error(f"Stack trace: {traceback.format_exc()}")
        finally:
            eventlet.sleep(30)  # Update every 30 seconds

# Start the update scheduler
eventlet.spawn(schedule_updates)

def setup_database():
    """Initialize database and create initial data"""
    try:
        # Create database directory if it doesn't exist
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'digital_twin.db')
        db_dir = os.path.dirname(db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir)
        
        # Create all tables
        with app.app_context():
            db.create_all()
            
            # Check if machines exist
            if not Machine.query.first():
                # Add default machines
                machines = [
                    Machine(name='Leadwell1', status='available'),
                ]
                db.session.add_all(machines)
                db.session.commit()
                app.logger.info("Default machines created")
            
            # Check if admin user exists
            if not User.query.filter_by(username='admin').first():
                # Create admin user
                admin = User(
                    username='admin',
                    password=generate_password_hash('admin123'),
                    role='admin',
                    is_active=True
                )
                db.session.add(admin)
                db.session.commit()
                app.logger.info("Admin user created")
            
            app.logger.info("Database initialized successfully")
            return True
            
    except Exception as e:
        app.logger.error(f"Error initializing database: {str(e)}")
        db.session.rollback()
        return False

@app.route('/check_drawing/<drawing_number>')
def check_drawing(drawing_number):
    """Debug route to check if a drawing exists"""
    try:
        if not os.path.exists('digital_twin.db'):
            app.logger.info("Database file not found. Initializing...")
            if initialize_application():
                app.logger.info("Database initialized successfully")
            else:
                app.logger.error("Failed to initialize database")
                return jsonify({'error': 'Failed to initialize database'}), 500
        else:
            # Still run initialization to ensure all required data exists
            if initialize_application():
                app.logger.info("Database verified successfully")
            else:
                app.logger.error("Failed to verify database")
                return jsonify({'error': 'Failed to verify database'}), 500
        
        # Check if drawing exists
        drawing = MachineDrawing.query.filter_by(drawing_number=drawing_number).first()
        if drawing:
            return jsonify({
                'exists': True,
                'drawing_number': drawing.drawing_number,
                'sap_id': drawing.sap_id
            })
        return jsonify({'exists': False})
    except Exception as e:
        app.logger.error(f"Error checking drawing: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/search_drawing/<sap_id>')
@login_required
def search_drawing_by_sap(sap_id):
    """Search for a drawing by SAP ID and return its production details"""
    try:
        # Clear any cached data
        db.session.expire_all()
        
        drawing = MachineDrawing.query.filter_by(sap_id=sap_id).first()
        if not drawing:
            return jsonify({'exists': False})

        # Get real-time production details
        completed = db.session.query(func.coalesce(func.sum(OperatorLog.run_completed_quantity), 0))\
            .filter(OperatorLog.end_product_sap_id == drawing.sap_id)\
            .scalar() or 0
        
        rejected = db.session.query(func.coalesce(func.sum(
            OperatorLog.run_rejected_quantity_fpi + OperatorLog.run_rejected_quantity_lpi), 0))\
            .filter(OperatorLog.end_product_sap_id == drawing.sap_id)\
            .scalar() or 0
        
        rework = db.session.query(func.coalesce(func.sum(
            OperatorLog.run_rework_quantity_fpi + OperatorLog.run_rework_quantity_lpi), 0))\
            .filter(OperatorLog.end_product_sap_id == drawing.sap_id)\
            .scalar() or 0

        # Get latest operator log for this drawing
        latest_log = OperatorLog.query\
            .filter_by(drawing_id=drawing.id)\
            .order_by(OperatorLog.created_at.desc())\
            .first()

        # Get end product details
        end_product = EndProduct.query.filter_by(sap_id=drawing.sap_id).first()

        return jsonify({
            'exists': True,
            'drawing_number': drawing.drawing_number,
            'sap_id': drawing.sap_id,
            'project_name': end_product.project_rel.project_name if end_product and end_product.project_rel else 'N/A',
            'end_product': end_product.name if end_product else 'N/A',
            'planned_quantity': end_product.quantity if end_product else 0,
            'completed_quantity': completed,
            'rejected_quantity': rejected,
            'rework_quantity': rework,
            'completion_percentage': round((completed / end_product.quantity * 100), 1) if end_product and end_product.quantity else 0,
            'status': latest_log.current_status if latest_log else 'N/A',
            'last_operation': {
                'operator': latest_log.operator_id if latest_log else 'N/A',
                'machine': latest_log.machine_id if latest_log else 'N/A',
                'status': latest_log.current_status if latest_log else 'N/A',
                'timestamp': latest_log.created_at.strftime('%Y-%m-%d %H:%M:%S') if latest_log else 'N/A'
            } if latest_log else None
        })
    except Exception as e:
        app.logger.error(f"Error searching drawing: {str(e)}")
        return jsonify({'error': str(e)})

@app.route('/digital_twin')
@login_required
def digital_twin_dashboard():
    """Handle digital twin dashboard with proper access control"""
    try:
        # Check role-based access
        if not current_user.is_authenticated:
            flash('Please log in to access the Digital Twin.', 'warning')
            return redirect(url_for('login_general'))
            
        allowed_roles = ['plant_head', 'manager', 'planner']
        active_role = session.get('active_role')
        
        if not active_role:
            flash('No active role found. Please log in again.', 'warning')
            return redirect(url_for('login_general'))
            
        if active_role not in allowed_roles:
            flash('Access denied. You do not have permission to view the Digital Twin.', 'warning')
            return redirect(url_for('home'))

        # Clear any cached data
        db.session.expire_all()

        machines = Machine.query.all()
        if not machines:
            flash('No machines found in the system.', 'warning')
            return render_template('digital_twin.html', machines=[], machine_data=[])

        machine_data = []
        now = datetime.now(timezone.utc)

        for machine in machines:
            try:
                # Get active operator session
                active_session = OperatorSession.query.filter_by(
                    machine_id=machine.id,
                    is_active=True
                ).first()

                # If session exists, ensure login_time is timezone-aware
                if active_session and active_session.login_time:
                    if active_session.login_time.tzinfo is None:
                        active_session.login_time = active_session.login_time.replace(tzinfo=timezone.utc)

                # Calculate OEE components using the machine utilization function
                try:
                    metrics = calculate_machine_utilization(machine)
                except Exception as metrics_error:
                    app.logger.error(f"Error calculating metrics for machine {machine.name}: {str(metrics_error)}")
                    metrics = {
                        'availability': 0,
                        'performance': 0,
                        'quality': 0,
                        'oee': 0
                    }

                # Get current operator log
                current_log = None
                if active_session:
                    current_log = OperatorLog.query.filter(
                        OperatorLog.operator_session_id == active_session.id,
                        OperatorLog.current_status.notin_(['cycle_completed', 'admin_closed'])
                    ).order_by(OperatorLog.created_at.desc()).first()
                
                # Get current drawing
                current_drawing = None
                if current_log:
                    current_drawing = MachineDrawing.query.get(current_log.drawing_id)

                # Calculate setup and cycle times
                setup_times = db.session.query(
                    func.avg(OperatorLog.setup_time).label('avg_setup'),
                    func.min(OperatorLog.setup_time).label('min_setup'),
                    func.max(OperatorLog.setup_time).label('max_setup')
                ).filter(
                    OperatorLog.machine_id == machine.id,
                    OperatorLog.setup_time.isnot(None)
                ).first()

                cycle_times = db.session.query(
                    func.avg(OperatorLog.cycle_time).label('avg_cycle'),
                    func.min(OperatorLog.cycle_time).label('min_cycle'),
                    func.max(OperatorLog.cycle_time).label('max_cycle')
                ).filter(
                    OperatorLog.machine_id == machine.id,
                    OperatorLog.cycle_time.isnot(None)
                ).first()

                # Get parts completed in current session
                parts_completed = 0
                if active_session:
                    parts_completed = db.session.query(func.sum(OperatorLog.run_completed_quantity))\
                        .filter(OperatorLog.operator_session_id == active_session.id)\
                        .scalar() or 0

                # Get latest production record for current product
                latest_record = ProductionRecord.query.filter_by(machine_id=machine.id)\
                    .order_by(ProductionRecord.timestamp.desc()).first()
                
                current_product = None
                if latest_record and latest_record.end_product:
                    current_product = latest_record.end_product.name

                # Ensure all timestamps are timezone-aware
                last_updated = latest_record.timestamp if latest_record else None
                if last_updated and last_updated.tzinfo is None:
                    last_updated = last_updated.replace(tzinfo=timezone.utc)
                
                machine_data.append({
                    'name': machine.name,
                    'status': machine.status,
                    'operator': active_session.operator_name if active_session else 'N/A',
                    'oee': {
                        'availability': metrics['availability'] * 100,  # Convert to percentage
                        'performance': metrics['performance'] * 100,
                        'quality': metrics['quality'] * 100,
                        'overall': metrics['oee'] * 100
                    },
                    'current_operator': {
                        'name': active_session.operator_name if active_session else None,
                        'shift': active_session.shift if active_session else None
                    } if active_session else None,
                    'current_job': {
                        'drawing_number': current_drawing.drawing_number if current_drawing else None,
                        'sap_id': current_drawing.sap_id if current_drawing else None,
                        'product_name': current_product
                    } if current_drawing else None,
                    'current_session': {
                        'start_time': active_session.login_time if active_session else None,
                        'parts_completed': parts_completed
                    } if active_session else None,
                    'actual_setup_time_display': f"{setup_times.avg_setup:.1f} min" if setup_times and setup_times.avg_setup else None,
                    'std_setup_time': setup_times.avg_setup if setup_times and setup_times.avg_setup else None,
                    'avg_actual_cycle_time_display': f"{cycle_times.avg_cycle:.2f} min/pc" if cycle_times and cycle_times.avg_cycle else None,
                    'std_cycle_time': cycle_times.avg_cycle if cycle_times and cycle_times.avg_cycle else None,
                    'last_updated': last_updated
                })
            except Exception as machine_error:
                app.logger.error(f"Error processing machine {machine.name}: {str(machine_error)}")
                # Add a placeholder entry for the failed machine
                machine_data.append({
                    'name': machine.name,
                    'status': 'error',
                    'error_message': 'Error loading machine data',
                    'oee': {
                        'availability': 0,
                        'performance': 0,
                        'quality': 0,
                        'overall': 0
                    }
                })

        return render_template('digital_twin.html',
            machine_data=machine_data,
            now=now,
            last_updated_time=now
        )
    except Exception as e:
        app.logger.error(f"Error in digital twin dashboard: {str(e)}")
        app.logger.error(f"Stack trace: {traceback.format_exc()}")
        flash('An error occurred while loading the Digital Twin dashboard.', 'danger')
        return render_template('digital_twin.html', 
            machine_data=[],
            error_message='Error loading dashboard data. Please try again later.'
        )

@app.route('/machine_report', methods=['GET'])
def machine_report():
    if 'active_role' not in session or session['active_role'] not in ['manager', 'planner', 'plant_head']:
        flash('Access denied. Only managers, planners, and plant heads can access reports.', 'danger')
        return redirect(url_for('login_general'))

    # Get date range from query parameters or default to today
    start_date = request.args.get('start_date', datetime.now(timezone.utc).date().isoformat())
    end_date = request.args.get('end_date', start_date)
    
    # Convert string dates to datetime
    start_dt = datetime.fromisoformat(start_date)
    end_dt = datetime.fromisoformat(end_date) + timedelta(days=1)  # Include full end date

    machines = Machine.query.order_by(Machine.name).all()
    report_data = []

    for machine in machines:
        # Get all operator logs for this machine in date range
        logs = OperatorLog.query.join(OperatorSession).filter(
            OperatorSession.machine_id == machine.id,
            OperatorLog.setup_start_time >= start_dt,
            OperatorLog.setup_start_time < end_dt
        ).options(
            db.joinedload(OperatorLog.drawing_rel),
            db.joinedload(OperatorLog.operator_session),
            db.joinedload(OperatorLog.quality_checks)
        ).all()

        for log in logs:
            # Calculate times
            setup_time = None
            if log.setup_start_time and log.setup_end_time:
                setup_time = (log.setup_end_time - log.setup_start_time).total_seconds() / 60

            cycle_time = None
            total_cycle_time = 0
            if log.first_cycle_start_time and log.last_cycle_end_time and log.run_completed_quantity:
                total_cycle_time = (log.last_cycle_end_time - log.first_cycle_start_time).total_seconds() / 60
                cycle_time = total_cycle_time / log.run_completed_quantity if log.run_completed_quantity > 0 else None

            # Calculate OEE components
            availability = 0
            performance = 0
            quality = 0
            oee = 0

            if log.drawing_rel and log.drawing_rel.end_product_rel:
                std_setup_time = log.drawing_rel.end_product_rel.setup_time_std or 0
                std_cycle_time = log.drawing_rel.end_product_rel.cycle_time_std or 0
                
                # Performance calculation
                if std_cycle_time > 0 and cycle_time:
                    performance = (std_cycle_time / cycle_time) * 100
                
                # Quality calculation
                total_parts = (log.run_completed_quantity or 0) + (log.run_rejected_quantity_fpi or 0) + \
                            (log.run_rejected_quantity_lpi or 0) + (log.run_rework_quantity_fpi or 0) + \
                            (log.run_rework_quantity_lpi or 0)
                if total_parts > 0:
                    quality = ((log.run_completed_quantity or 0) / total_parts) * 100

                # Availability calculation (simplified)
                if log.current_status == 'cycle_started':
                    availability = 95.0
                elif log.current_status == 'cycle_paused':
                    availability = 80.0
                elif log.current_status in ['setup_started', 'setup_done']:
                    availability = 75.0

                # Overall OEE
                oee = (availability * performance * quality) / 10000

            # Build report row matching spreadsheet format
            row = {
                'date': log.setup_start_time.date(),
                'shift': log.operator_session.shift,
                'machine': machine.name,
                'operator': log.operator_session.operator_name,
                'drawing': log.drawing_rel.drawing_number if log.drawing_rel else 'N/A',
                'tool_change': 0,  # Add if you track this
                'inspection': 0,  # Add if you track this
                'engagement': 0,  # Add if you track this
                'rework': (log.run_rework_quantity_fpi or 0) + (log.run_rework_quantity_lpi or 0),
                'minor_stoppage': 0,  # Add if you track this
                'setup_time': round(setup_time, 2) if setup_time else 0,
                'tea_break': 0,  # Add if you track this
                'tbt': 0,  # Add if you track this
                'lunch': 0,  # Add if you track this
                '5s': 0,  # Add if you track this
                'pm': 0,  # Add if you track this
                'planned_qty': log.run_planned_quantity,
                'completed_qty': log.run_completed_quantity,
                'std_setup_time': std_setup_time,
                'std_cycle_time': std_cycle_time,
                'actual_setup_time': round(setup_time, 2) if setup_time else 0,
                'actual_cycle_time': round(cycle_time, 2) if cycle_time else 0,
                'availability': round(availability, 2),
                'performance': round(performance, 2),
                'quality': round(quality, 2),
                'oee': round(oee, 2),
                'status': log.current_status,
                'quality_status': "Pending FPI" if log.current_status == 'cycle_completed_pending_fpi' \
                                else "Pending LPI" if log.current_status == 'cycle_completed_pending_lpi' \
                                else "N/A",
                'reason': '',
                'machine_power': 'ON' if log.current_status not in ['admin_closed'] else 'OFF',
                'program_issues': ''  # Add if you track programming issues
            }
            report_data.append(row)

    return render_template('machine_report.html', 
                         report_data=report_data,
                         start_date=start_date,
                         end_date=end_date)

@app.route('/machine_report/download', methods=['POST'])
def download_machine_report():
    if 'active_role' not in session or session['active_role'] not in ['manager', 'planner', 'plant_head']:
        flash('Access denied. Only managers, planners, and plant heads can access reports.', 'danger')
        return redirect(url_for('login_general'))

    # Get date range from form data
    start_date = request.form.get('start_date', datetime.now(timezone.utc).date().isoformat())
    end_date = request.form.get('end_date', start_date)
    start_dt = datetime.fromisoformat(start_date)
    end_dt = datetime.fromisoformat(end_date) + timedelta(days=1)

    machines = Machine.query.order_by(Machine.name).all()
    report_data = []

    for machine in machines:
        logs = OperatorLog.query.join(OperatorSession).filter(
            OperatorSession.machine_id == machine.id,
            OperatorLog.setup_start_time >= start_dt,
            OperatorLog.setup_start_time < end_dt
        ).options(
            db.joinedload(OperatorLog.drawing_rel),
            db.joinedload(OperatorLog.operator_session),
            db.joinedload(OperatorLog.quality_checks)
        ).all()

        for log in logs:
            setup_time = None
            if log.setup_start_time and log.setup_end_time:
                setup_time = (log.setup_end_time - log.setup_start_time).total_seconds() / 60
            cycle_time = None
            total_cycle_time = 0
            if log.first_cycle_start_time and log.last_cycle_end_time and log.run_completed_quantity:
                total_cycle_time = (log.last_cycle_end_time - log.first_cycle_start_time).total_seconds() / 60
                cycle_time = total_cycle_time / log.run_completed_quantity if log.run_completed_quantity > 0 else None
            availability = 0
            performance = 0
            quality = 0
            oee = 0
            if log.drawing_rel and log.drawing_rel.end_product_rel:
                std_setup_time = log.drawing_rel.end_product_rel.setup_time_std or 0
                std_cycle_time = log.drawing_rel.end_product_rel.cycle_time_std or 0
                if std_cycle_time > 0 and cycle_time:
                    performance = (std_cycle_time / cycle_time) * 100
                total_parts = (log.run_completed_quantity or 0) + (log.run_rejected_quantity_fpi or 0) + \
                            (log.run_rejected_quantity_lpi or 0) + (log.run_rework_quantity_fpi or 0) + \
                            (log.run_rework_quantity_lpi or 0)
                if total_parts > 0:
                    quality = ((log.run_completed_quantity or 0) / total_parts) * 100
                if log.current_status == 'cycle_started':
                    availability = 95.0
                elif log.current_status == 'cycle_paused':
                    availability = 80.0
                elif log.current_status in ['setup_started', 'setup_done']:
                    availability = 75.0
                oee = (availability * performance * quality) / 10000
            row = {
                'date': log.setup_start_time.date(),
                'shift': log.operator_session.shift,
                'machine': machine.name,
                'operator': log.operator_session.operator_name,
                'drawing': log.drawing_rel.drawing_number if log.drawing_rel else 'N/A',
                'tool_change': 0,
                'inspection': 0,
                'engagement': 0,
                'rework': (log.run_rework_quantity_fpi or 0) + (log.run_rework_quantity_lpi or 0),
                'minor_stoppage': 0,
                'setup_time': round(setup_time, 2) if setup_time else 0,
                'tea_break': 0,
                'tbt': 0,
                'lunch': 0,
                '5s': 0,
                'pm': 0,
                'planned_qty': log.run_planned_quantity,
                'completed_qty': log.run_completed_quantity,
                'std_setup_time': std_setup_time,
                'std_cycle_time': std_cycle_time,
                'actual_setup_time': round(setup_time, 2) if setup_time else 0,
                'actual_cycle_time': round(cycle_time, 2) if cycle_time else 0,
                'availability': round(availability, 2),
                'performance': round(performance, 2),
                'quality': round(quality, 2),
                'oee': round(oee, 2),
                'status': log.current_status,
                'quality_status': "Pending FPI" if log.current_status == 'cycle_completed_pending_fpi' \
                                else "Pending LPI" if log.current_status == 'cycle_completed_pending_lpi' \
                                else "N/A",
                'reason': '',
                'machine_power': 'ON' if log.current_status not in ['admin_closed'] else 'OFF',
                'program_issues': ''
            }
            report_data.append(row)

    # Explicitly set column order and names
    columns = [
        'date', 'shift', 'machine', 'operator', 'drawing', 'tool_change', 'inspection', 'engagement', 'rework',
        'minor_stoppage', 'setup_time', 'tea_break', 'tbt', 'lunch', '5s', 'pm', 'planned_qty', 'completed_qty',
        'std_setup_time', 'std_cycle_time', 'actual_setup_time', 'actual_cycle_time', 'availability', 'performance',
        'quality', 'oee', 'status', 'quality_status', 'reason', 'machine_power', 'program_issues'
    ]
    import pandas as pd
    df = pd.DataFrame(report_data, columns=columns)
    if df.empty:
        df = pd.DataFrame(columns=columns)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, sheet_name='Machine Report', index=False, header=True)
    output.seek(0)
    file_name = f"machine_report_{start_date}_to_{end_date}.xlsx"
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=file_name
    )

def restore_quality_session(inspector_name):
    """Restore quality inspector's previous session state"""
    try:
        recent_checks = QualityCheck.query.filter_by(
            inspector_name=inspector_name
        ).order_by(QualityCheck.timestamp.desc()).limit(5).all()
        
        if recent_checks:
            return {
                'recent_checks': [{
                    'timestamp': check.timestamp,
                    'check_type': check.check_type,
                    'result': check.result,
                    'drawing_number': check.operator_log.drawing_rel.drawing_number if check.operator_log and check.operator_log.drawing_rel else 'N/A'
                } for check in recent_checks]
            }
        return None
    except Exception as e:
        app.logger.error(f"Error restoring quality session: {str(e)}")
        return None

@app.route('/quality', methods=['GET', 'POST'])
def quality_dashboard():
    if session.get('active_role') != 'quality':
        flash('Access denied. Please login as Quality Inspector.', 'danger')
        return redirect(url_for('login_general'))

    # Configure session to be permanent and set a long expiry
    session.permanent = True
    app.permanent_session_lifetime = timedelta(days=30)  # Set session to last 30 days

    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'set_inspector_name':
            inspector_name = request.form.get('inspector_name', '').strip()
            if inspector_name:
                session['quality_inspector_name'] = inspector_name
                # Restore previous quality session state
                previous_state = restore_quality_session(inspector_name)
                if previous_state and previous_state['recent_checks']:
                    recent_checks_info = previous_state['recent_checks']
                    flash(f'Welcome back! You have performed {len(recent_checks_info)} recent quality checks.', 'info')
                flash('Inspector name set successfully.', 'success')
            else:
                flash('Inspector name cannot be empty.', 'warning')
            return redirect(url_for('quality_dashboard'))

        if action == 'update_inspector_name':
            inspector_name = request.form.get('inspector_name', '').strip()
            if inspector_name:
                session['quality_inspector_name'] = inspector_name
                flash('Inspector name updated successfully.', 'success')
            else:
                flash('Inspector name cannot be empty.', 'warning')
            return redirect(url_for('quality_dashboard'))

        # For all other actions, ensure inspector name is set
        if not session.get('quality_inspector_name'):
            flash('Please set your inspector name first.', 'warning')
            return redirect(url_for('quality_dashboard'))
        
        # Handle the new simplified quality check form
        if action == 'simple_quality_check':
            drawing_number = request.form.get('drawing_number', '').strip()
            check_type = request.form.get('check_type')  # FPI or LPI
            result = request.form.get('result')  # pass, rework, or reject
            rejection_reason = request.form.get('rejection_reason', '').strip()
            log_id = request.form.get('log_id')  # May be provided if clicked from table
            
            # Validate inputs
            if not drawing_number:
                flash('Drawing number is required.', 'danger')
                return redirect(url_for('quality_dashboard'))
                
            if not check_type or check_type not in ['FPI', 'LPI']:
                flash('Valid check type (FPI or LPI) is required.', 'danger')
                return redirect(url_for('quality_dashboard'))
                
            if not result or result not in ['pass', 'rework', 'reject']:
                flash('Valid result (pass, rework, or reject) is required.', 'danger')
                return redirect(url_for('quality_dashboard'))
            
            # If log_id is provided, use that specific log
            if log_id:
                op_log_to_inspect = OperatorLog.query.get(log_id)
                if not op_log_to_inspect:
                    flash('Operator log not found.', 'danger')
                    return redirect(url_for('quality_dashboard'))
                    
                # Verify the drawing number matches
                if op_log_to_inspect.drawing_rel and op_log_to_inspect.drawing_rel.drawing_number != drawing_number:
                    flash(f'Drawing number mismatch. Expected {op_log_to_inspect.drawing_rel.drawing_number}, got {drawing_number}.', 'danger')
                    return redirect(url_for('quality_dashboard'))
                    
                # Verify the log is in the correct state for the check type
                if check_type == 'FPI' and op_log_to_inspect.current_status != 'cycle_completed_pending_fpi':
                    flash('This log is not pending FPI.', 'warning')
                    return redirect(url_for('quality_dashboard'))
                elif check_type == 'LPI' and op_log_to_inspect.current_status != 'cycle_completed_pending_lpi':
                    flash('This log is not pending LPI.', 'warning')
                    return redirect(url_for('quality_dashboard'))
            else:
                # No log_id provided, find a matching log based on drawing number and check type
                drawing = MachineDrawing.query.filter_by(drawing_number=drawing_number).first()
                if not drawing:
                    flash(f'Drawing number {drawing_number} not found.', 'danger')
                    return redirect(url_for('quality_dashboard'))
                
                if check_type == 'FPI':
                    op_log_to_inspect = OperatorLog.query.filter_by(
                        drawing_id=drawing.id,
                        current_status='cycle_completed_pending_fpi'
                    ).order_by(OperatorLog.created_at.desc()).first()
                else:  # LPI
                    op_log_to_inspect = OperatorLog.query.filter_by(
                        drawing_id=drawing.id,
                        current_status='cycle_completed_pending_lpi'
                    ).order_by(OperatorLog.created_at.desc()).first()
                
                if not op_log_to_inspect:
                    flash(f'No pending {check_type} found for drawing {drawing_number}.', 'warning')
                    return redirect(url_for('quality_dashboard'))
            
            # Process based on check type
            if check_type == 'FPI':
                # Create quality check record first
                new_qc_record = QualityCheck(
                    operator_log_id=op_log_to_inspect.id,
                    inspector_name=session.get('quality_inspector_name'),
                    check_type='FPI',
                    result=result,
                    rejection_reason=rejection_reason if result in ['reject', 'rework'] else None
                )
                db.session.add(new_qc_record)
                db.session.flush()  # Get the ID before using it

                # FPI can be repeated until passed
                if result == 'pass':
                    op_log_to_inspect.fpi_status = 'pass'
                    op_log_to_inspect.production_hold_fpi = False
                    op_log_to_inspect.current_status = 'fpi_passed_ready_for_cycle'
                    flash('FPI passed. Operator can continue production.', 'success')
                elif result == 'reject':
                    op_log_to_inspect.fpi_status = 'fail'
                    op_log_to_inspect.production_hold_fpi = True
                    op_log_to_inspect.current_status = 'cycle_completed_pending_fpi'  # Stay pending FPI
                    op_log_to_inspect.run_rejected_quantity_fpi = (op_log_to_inspect.run_rejected_quantity_fpi or 0) + 1
                    # Create scrap record for rejected FPI
                    scrap_record = ScrapLog(
                        drawing_id=op_log_to_inspect.drawing_id,
                        quantity_scrapped=1,
                        reason=f"FPI Rejected: {rejection_reason}",
                        scrapped_at=datetime.now(timezone.utc),
                        scrapped_by=session.get('quality_inspector_name'),
                        operator_log_id=op_log_to_inspect.id,
                        originating_quality_check_id=new_qc_record.id
                    )
                    db.session.add(scrap_record)
                    flash('FPI failed. Parts marked as scrap. Repeat FPI on next piece.', 'warning')
                elif result == 'rework':
                    op_log_to_inspect.fpi_status = 'rework'
                    op_log_to_inspect.production_hold_fpi = True
                    op_log_to_inspect.current_status = 'cycle_completed_pending_fpi'  # Stay pending FPI
                    op_log_to_inspect.run_rework_quantity_fpi = (op_log_to_inspect.run_rework_quantity_fpi or 0) + 1
                    db.session.flush()

                    # Create rework queue item
                    rework_item = ReworkQueue(
                        source_operator_log_id=op_log_to_inspect.id,
                        originating_quality_check_id=new_qc_record.id,
                        drawing_id=op_log_to_inspect.drawing_id,
                        quantity=1,
                        status='pending_manager_approval'  # Explicitly set status
                    )
                    db.session.add(rework_item)
                    flash('FPI failed. Parts sent for rework approval.', 'warning')
            
            else:  # LPI
                # Get quantities for LPI
                quantity_inspected = request.form.get('quantity_inspected', type=int, default=1)
                quantity_rejected = request.form.get('quantity_rejected', type=int, default=0)
                quantity_to_rework = request.form.get('quantity_to_rework', type=int, default=0)
                
                # Validate quantities
                if quantity_inspected > op_log_to_inspect.run_completed_quantity:
                    flash('Cannot inspect more parts than were produced.', 'warning')
                    return redirect(url_for('quality_dashboard'))

                if quantity_rejected and quantity_rejected > quantity_inspected:
                    flash('Cannot reject more parts than were inspected.', 'warning')
                    return redirect(url_for('quality_dashboard'))

                if quantity_to_rework and quantity_to_rework > quantity_inspected:
                    flash('Cannot rework more parts than were inspected.', 'warning')
                    return redirect(url_for('quality_dashboard'))

                if quantity_rejected and quantity_to_rework and (quantity_rejected + quantity_to_rework) > quantity_inspected:
                    flash('Total of rejected and rework parts cannot exceed inspected quantity.', 'warning')
                    return redirect(url_for('quality_dashboard'))

                # Create quality check record first
                new_qc_record = QualityCheck(
                    operator_log_id=op_log_to_inspect.id,
                    inspector_name=session.get('quality_inspector_name'),
                    check_type='LPI',
                    result=result,
                    rejection_reason=rejection_reason if result in ['reject', 'rework'] else None,
                    lpi_quantity_inspected=quantity_inspected,
                    lpi_quantity_rejected=quantity_rejected,
                    lpi_quantity_to_rework=quantity_to_rework
                )
                db.session.add(new_qc_record)
                db.session.flush()  # Get the ID before using it

                # LPI can be repeated until passed
                if result == 'pass':
                    op_log_to_inspect.lpi_status = 'pass'
                    op_log_to_inspect.current_status = 'lpi_completed'
                    flash('LPI passed. Production cycle complete.', 'success')
                elif result == 'reject':
                    op_log_to_inspect.lpi_status = 'fail'
                    op_log_to_inspect.current_status = 'cycle_completed_pending_lpi'  # Stay pending LPI
                    op_log_to_inspect.run_rejected_quantity_lpi = quantity_rejected
                    # Create scrap record for rejected parts
                    scrap_record = ScrapLog(
                        drawing_id=op_log_to_inspect.drawing_id,
                        quantity_scrapped=quantity_rejected,
                        reason=f"LPI Rejected: {rejection_reason}",
                        scrapped_at=datetime.now(timezone.utc),
                        scrapped_by=session.get('quality_inspector_name'),
                        operator_log_id=op_log_to_inspect.id,
                        originating_quality_check_id=new_qc_record.id
                    )
                    db.session.add(scrap_record)
                    flash(f'LPI failed. {quantity_rejected} parts marked as scrap. Repeat LPI on next piece.', 'warning')
                elif result == 'rework':
                    op_log_to_inspect.lpi_status = 'rework'
                    op_log_to_inspect.current_status = 'cycle_completed_pending_lpi'  # Stay pending LPI
                    op_log_to_inspect.run_rework_quantity_lpi = quantity_to_rework
                    if quantity_rejected > 0:
                        # Create scrap record for rejected parts
                        scrap_record = ScrapLog(
                            drawing_id=op_log_to_inspect.drawing_id,
                            quantity_scrapped=quantity_rejected,
                            reason=f"LPI Rejected: {rejection_reason}",
                            scrapped_at=datetime.now(timezone.utc),
                            scrapped_by=session.get('quality_inspector_name'),
                            operator_log_id=op_log_to_inspect.id,
                            originating_quality_check_id=new_qc_record.id
                        )
                        db.session.add(scrap_record)

                    if quantity_to_rework > 0:
                        # Create rework queue item
                        rework_item = ReworkQueue(
                            source_operator_log_id=op_log_to_inspect.id,
                            originating_quality_check_id=new_qc_record.id,
                            drawing_id=op_log_to_inspect.drawing_id,
                            quantity=quantity_to_rework,
                            status='pending_manager_approval'  # Explicitly set status
                        )
                        db.session.add(rework_item)

                    flash(f'LPI completed. {quantity_rejected} parts scrapped, {quantity_to_rework} parts sent for rework approval.', 'warning')
            
            db.session.commit()
            emit_quality_update()  # Emit update for real-time updates
            return redirect(url_for('quality_dashboard'))

    # GET request - prepare data for template
    inspector_name = session.get('quality_inspector_name')
    
    # If no inspector name in session, try to restore from previous quality checks
    if not inspector_name:
        # Get the most recent quality check to find the last inspector
        last_check = QualityCheck.query.order_by(QualityCheck.timestamp.desc()).first()
        if last_check:
            inspector_name = last_check.inspector_name
            session['quality_inspector_name'] = inspector_name
            flash(f'Welcome back {inspector_name}! Your previous inspector name has been restored.', 'info')

    # Get recent quality checks for metrics
    recent_quality_checks = QualityCheck.query.filter(
        QualityCheck.inspector_name == inspector_name
    ).order_by(QualityCheck.timestamp.desc()).limit(10).all() if inspector_name else []

    # Get pending FPI and LPI logs
    pending_fpi_logs = OperatorLog.query.join(
        OperatorSession
    ).join(
        MachineDrawing
    ).filter(
        OperatorLog.current_status == 'cycle_completed_pending_fpi'
    ).order_by(
        OperatorLog.created_at.desc()
    ).all()

    pending_lpi_logs = OperatorLog.query.join(
        OperatorSession
    ).join(
        MachineDrawing
    ).filter(
        OperatorLog.current_status == 'cycle_completed_pending_lpi'
    ).order_by(
        OperatorLog.created_at.desc()
    ).all()

    # Prepare logs data for JavaScript
    def serialize_log(log):
        return {
            "id": log.id,
            "drawing_rel": {
                "drawing_number": log.drawing_rel.drawing_number if log.drawing_rel else "N/A"
            } if hasattr(log, "drawing_rel") and log.drawing_rel else None,
            "operator_session_rel": {
                "operator_name": log.operator_session_rel.operator_name if log.operator_session_rel else "N/A",
                "machine_rel": {
                    "name": log.operator_session_rel.machine_rel.name if log.operator_session_rel and log.operator_session_rel.machine_rel else "N/A"
                } if log.operator_session_rel and hasattr(log.operator_session_rel, "machine_rel") and log.operator_session_rel.machine_rel else None
            } if hasattr(log, "operator_session_rel") and log.operator_session_rel else None,
            "current_status": log.current_status,
            "run_completed_quantity": log.run_completed_quantity,
            "run_planned_quantity": log.run_planned_quantity,
            "fpi_status": log.fpi_status,
            "lpi_status": log.lpi_status,
        }

    logs_for_js = [serialize_log(log) for log in pending_fpi_logs + pending_lpi_logs]

    return render_template(
        'quality.html',
        inspector_name=inspector_name,
        pending_fpi_logs=pending_fpi_logs,
        pending_lpi_logs=pending_lpi_logs,
        logs_for_js=logs_for_js,
        recent_quality_checks=recent_quality_checks
    )

# Add concurrent user management
from user_management import can_add_user, add_user_session, remove_user_session

@app.route('/plant_head_dashboard')
@login_required
def plant_head_dashboard():
    if not current_user.is_plant_head:
        flash('Access denied. Plant Head access required.', 'danger')
        return redirect(url_for('login_general'))

    try:
        # Clear any cached data
        db.session.expire_all()

        # Get machine OEE data
        machines = Machine.query.all()
        machine_names = [machine.name for machine in machines]
        oee_data = []
        oee_sum = 0
        
        for machine in machines:
            availability = calculate_availability(machine)
            performance = calculate_performance(machine)
            quality = calculate_quality(machine)
            oee = availability * performance * quality
            oee_percent = round(oee * 100, 2)
            oee_data.append(oee_percent)
            oee_sum += oee_percent
        
        average_oee = round(oee_sum / len(machines), 2) if machines else 0

        # Get shift production data with validation
        today = datetime.now(timezone.utc).date()
        shifts = ['Morning', 'Afternoon', 'Night']
        shift_data = []
        
        for shift in shifts:
            production = db.session.query(func.sum(OperatorLog.run_completed_quantity))\
                .filter(
                    func.date(OperatorLog.created_at) == today,
                    OperatorLog.shift == shift,
                    OperatorLog.run_completed_quantity > 0  # Only count valid quantities
                ).scalar() or 0
            shift_data.append(int(production))

        # Get production summary with validation
        production_summary = []
        for product in EndProduct.query.all():
            if not validate_production_data(product.sap_id, product.quantity):
                continue
                
            completed = db.session.query(func.coalesce(func.sum(OperatorLog.run_completed_quantity), 0))\
                .filter(
                    OperatorLog.end_product_sap_id == product.sap_id,
                    OperatorLog.run_completed_quantity > 0
                ).scalar() or 0
                
            rejected = db.session.query(func.coalesce(func.sum(
                OperatorLog.run_rejected_quantity_fpi + OperatorLog.run_rejected_quantity_lpi), 0))\
                .filter(
                    OperatorLog.end_product_sap_id == product.sap_id,
                    OperatorLog.run_rejected_quantity_fpi >= 0,
                    OperatorLog.run_rejected_quantity_lpi >= 0
                ).scalar() or 0
                
            rework = db.session.query(func.coalesce(func.sum(
                OperatorLog.run_rework_quantity_fpi + OperatorLog.run_rework_quantity_lpi), 0))\
                .filter(
                    OperatorLog.end_product_sap_id == product.sap_id,
                    OperatorLog.run_rework_quantity_fpi >= 0,
                    OperatorLog.run_rework_quantity_lpi >= 0
                ).scalar() or 0
            
            production_summary.append({
                "Product": product.name,
                "SAP ID": product.sap_id,
                "Planned Qty": product.quantity,
                "Completed Qty": completed,
                "Rejected Qty": rejected,
                "Rework Qty": rework,
                "Completion %": round((completed / product.quantity * 100), 1) if product.quantity else 0
            })

        # Get real-time quality data
        recent_quality_checks = QualityCheck.query.order_by(QualityCheck.timestamp.desc()).limit(5).all()

        # Calculate today's production count
        todays_production_count = db.session.query(func.coalesce(func.sum(OperatorLog.run_completed_quantity), 0))\
            .filter(
                func.date(OperatorLog.created_at) == today,
                OperatorLog.run_completed_quantity > 0
            ).scalar() or 0

        # Calculate pending quality checks
        pending_quality_checks = QualityCheck.query.filter_by(result='pending').count()

        # Calculate machine utilization
        machine_utilization = round(calculate_overall_machine_utilization() * 100, 1)

        # Calculate timeline dates and OEE trend (last 7 days)
        timeline_dates = [(today - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(7)]
        oee_trend = [calculate_daily_oee(date) for date in timeline_dates]

        # Calculate machine status (running, setup, stopped)
        machine_running = [1 if get_machine_current_status(machine.id) == 'running' else 0 for machine in machines]
        machine_setup = [1 if get_machine_current_status(machine.id) == 'setup' else 0 for machine in machines]
        machine_stopped = [1 if get_machine_current_status(machine.id) == 'stopped' else 0 for machine in machines]

        # Calculate quality metrics
        quality_metrics = calculate_quality_metrics()

        return render_template('plant_head.html',
            oee_data=oee_data,
            shift_data=shift_data,
            machine_names=machine_names,
            shift_labels=shifts,
            production_summary=production_summary,
            recent_quality_checks=recent_quality_checks,
            todays_production_count=todays_production_count,
            pending_quality_checks=pending_quality_checks,
            machine_utilization=machine_utilization,
            average_oee=average_oee,
            digital_twin_url=url_for('digital_twin_dashboard'),
            timeline_dates=timeline_dates,
            oee_trend=oee_trend,
            machine_running=machine_running,
            machine_setup=machine_setup,
            machine_stopped=machine_stopped,
            quality_metrics=quality_metrics
        )
    except Exception as e:
        app.logger.error(f"Error in plant head dashboard: {str(e)}")
        flash('An error occurred while loading the dashboard.', 'danger')
        return redirect(url_for('login_general'))

# ... existing code ...

# Add SimpleUser class and user_loader before routes
class SimpleUser(UserMixin):
    def __init__(self, id, role):
        self.id = id
        self.role = role
        app.logger.debug(f"Created SimpleUser with id={id}, role={role}")

    @property
    def is_plant_head(self):
        return self.role == 'plant_head'

    @property
    def is_manager(self):
        return self.role == 'manager'

    @property
    def is_planner(self):
        return self.role == 'planner'

    @property
    def is_quality(self):
        return self.role == 'quality'

    @property
    def is_admin(self):
        return self.role == 'admin'

@login_manager.user_loader
def load_user(user_id):
    """Load user from user_id"""
    try:
        # The user_id is in format "role|role"
        role = user_id.split('|')[0]  # Both parts are the same now
        return SimpleUser(user_id, role)
    except (ValueError, AttributeError) as e:
        app.logger.error(f"Error loading user: {str(e)}")
        return None

def validate_production_data(sap_id, quantity):
    """Validate production data"""
    try:
        # Basic validation
        if not sap_id or not isinstance(sap_id, str):
            app.logger.warning(f"Invalid SAP ID format: {sap_id}")
            return False
            
        # Convert quantity to float for validation
        try:
            qty = float(quantity)
            if qty <= 0:
                app.logger.warning(f"Invalid quantity (must be positive): {quantity}")
                return False
        except (ValueError, TypeError):
            app.logger.warning(f"Invalid quantity format: {quantity}")
            return False
            
        # Additional validation if needed
        if len(sap_id) < 3 or len(sap_id) > 20:
            app.logger.warning(f"SAP ID length out of range: {sap_id}")
            return False
            
        if qty > 10000:  # Set a reasonable maximum
            app.logger.warning(f"Quantity exceeds maximum limit: {quantity}")
            return False
            
        return True
    except Exception as e:
        app.logger.error(f"Error validating production data: {str(e)}")
        return False

@app.route('/api/daily_production')
@login_required
def get_daily_production():
    try:
        today = datetime.now(timezone.utc).date()
        hours = ['08:00', '09:00', '10:00', '11:00', '12:00', '13:00', '14:00', '15:00', '16:00']
        production_data = []
        
        for hour in hours:
            hour_start = datetime.strptime(f"{today} {hour}", "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
            hour_end = hour_start.replace(minute=59, second=59)
            
            # Validate data before querying
            completed = db.session.query(func.coalesce(func.sum(OperatorLog.run_completed_quantity), 0))\
                .filter(
                    OperatorLog.created_at >= hour_start,
                    OperatorLog.created_at <= hour_end,
                    OperatorLog.run_completed_quantity > 0  # Only count valid quantities
                ).scalar() or 0
            
            production_data.append(int(completed))
        
        return jsonify(production_data)
    except Exception as e:
        app.logger.error(f"Error getting daily production data: {str(e)}")
        return jsonify([0] * 9)  # Return zeros for 9 hours if error

#2867

def get_timeline_data():
    """Get production timeline data for the current day"""
    try:
        today = datetime.now(timezone.utc).date()
        hours = ['08:00', '09:00', '10:00', '11:00', '12:00', '13:00', '14:00', '15:00', '16:00']
        planned_data = []
        actual_data = []
        
        # Get total planned for the day
        total_planned = db.session.query(func.sum(EndProduct.quantity))\
            .filter(func.date(EndProduct.created_at) == today).scalar() or 0
            
        # Calculate planned per hour (evenly distributed)
        hourly_planned = total_planned / len(hours) if total_planned > 0 else 0
        planned_data = [round(hourly_planned, 2)] * len(hours)
        
        # Get actual production by hour
        for hour in hours:
            hour_start = datetime.strptime(f"{today} {hour}", "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
            hour_end = hour_start.replace(minute=59, second=59)
            
            completed = db.session.query(func.coalesce(func.sum(OperatorLog.run_completed_quantity), 0))\
                .filter(
                    OperatorLog.created_at >= hour_start,
                    OperatorLog.created_at <= hour_end,
                    OperatorLog.run_completed_quantity > 0
                ).scalar() or 0
            
            actual_data.append(int(completed))
        
        return {
            'planned': planned_data,
            'actual': actual_data,
            'hours': hours
        }
    except Exception as e:
        app.logger.error(f"Error getting timeline data: {str(e)}")
        return {
            'planned': [0] * 9,
            'actual': [0] * 9,
            'hours': hours
        }

def emit_production_update():
    """Emit real-time production metrics updates"""
    with app.app_context():
        try:
            production_data = get_live_production_data(db)
            machine_data = get_machine_metrics(db)
            timeline_data = get_timeline_data()
            
            socketio.emit('production_update', {
                'data': production_data,
                'oee_data': [m.get('efficiency', 0) for m in machine_data],
                'shift_data': get_shift_production_data(),
                'timeline_data': timeline_data,
                'timestamp': datetime.now(timezone.utc).isoformat()
            })
        except Exception as e:
            app.logger.error(f"Error emitting production update: {str(e)}")

def get_shift_production_data():
    """Get production data by shift"""
    today = datetime.now(timezone.utc).date()
    shifts = ['Morning', 'Afternoon', 'Night']
    shift_data = []
    
    for shift in shifts:
        production = db.session.query(func.sum(OperatorLog.run_completed_quantity))\
            .filter(
                func.date(OperatorLog.created_at) == today,
                OperatorLog.shift == shift,
                OperatorLog.run_completed_quantity > 0
            ).scalar() or 0
        shift_data.append(int(production))
    
    return shift_data

# Initialize scheduler with proper timezone
scheduler = BackgroundScheduler(timezone=timezone.utc)
scheduler.add_job(
    func=emit_production_update,
    trigger="interval",
    seconds=30
)
scheduler.start()

# Ensure scheduler shuts down with Flask
atexit.register(lambda: scheduler.shutdown())

# Add validation function near other utils
def validate_drawing_number(drawing_number):
    """Validate drawing number format"""
    if not drawing_number or not isinstance(drawing_number, str):
        return False
    # Basic format check - adjust pattern as per your requirements
    return bool(re.match(r'^[A-Za-z0-9-]{3,50}$', drawing_number.strip()))

@app.route('/live_dpr')
@login_required
def live_dpr():
    """Handle live DPR page"""
    try:
        # Get initial DPR data with all columns
        dpr_data = get_live_production_data(db)
        
        # Get all active machines for filtering
        machines = Machine.query.order_by(Machine.name).all()
        
        # Add current time for date filter default
        now = datetime.now(timezone.utc)
        
        return render_template('live_dpr.html', 
            dpr_data=dpr_data,
            machines=machines,
            now=now
        )
    except Exception as e:
        app.logger.error(f"Error in live DPR page: {str(e)}")
        flash('An error occurred while loading the Live DPR.', 'danger')
        return redirect(url_for('home'))

@app.route('/live_dpr/download')
@login_required
def download_live_dpr():
    """Download live DPR data as Excel"""
    try:
        dpr_data = get_live_production_data(db)
        
        # Convert to pandas DataFrame
        df = pd.DataFrame(dpr_data)
        
        # Create Excel file in memory
        output = BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, sheet_name='Live DPR', index=False)
            
            # Get the workbook and worksheet objects
            workbook = writer.book
            worksheet = writer.sheets['Live DPR']
            
            # Add formats
            header_format = workbook.add_format({
                'bold': True,
                'bg_color': '#0066cc',
                'font_color': 'white',
                'border': 1
            })
            
            # Format the header row
            for col_num, value in enumerate(df.columns.values):
                worksheet.write(0, col_num, value, header_format)
                worksheet.set_column(col_num, col_num, 15)  # Set column width
        
        output.seek(0)
        
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=f'Live_DPR_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
        )
        
    except Exception as e:
        app.logger.error(f"Error downloading DPR: {str(e)}")
        flash('Error generating DPR download.', 'danger')
        return redirect(url_for('live_dpr'))

def calculate_project_completion(project):
    """Calculate completion percentage for a project"""
    try:
        total_quantity = 0
        completed_quantity = 0
        
        # Get all end products for this project
        end_products = EndProduct.query.filter_by(project_id=project.id).all()
        
        for product in end_products:
            total_quantity += product.quantity
            
            completed = db.session.query(
                func.sum(OperatorLog.run_completed_quantity)
            ).filter(
                OperatorLog.end_product_sap_id == product.sap_id,
                OperatorLog.quality_status == 'approved'
            ).scalar() or 0
            
            completed_quantity += completed
        
        return round((completed_quantity / total_quantity * 100), 1) if total_quantity > 0 else 0
    except Exception as e:
        app.logger.error(f"Error calculating project completion: {str(e)}")
        return 0

def calculate_daily_oee(date):
    """Calculate OEE for a specific date"""
    try:
        machines = Machine.query.all()
        total_oee = 0
        
        for machine in machines:
            # Calculate availability
            total_time = 24 * 60  # minutes in a day
            
            # Get all types of downtime
            planned_downtime = db.session.query(
                func.sum(OperatorLog.downtime_minutes)
            ).filter(
                OperatorLog.machine_id == machine.id,
                func.date(OperatorLog.created_at) == date,
                OperatorLog.downtime_category == 'planned'
            ).scalar() or 0
            
            unplanned_downtime = db.session.query(
                func.sum(OperatorLog.downtime_minutes)
            ).filter(
                OperatorLog.machine_id == machine.id,
                func.date(OperatorLog.created_at) == date,
                OperatorLog.downtime_category == 'unplanned'
            ).scalar() or 0
            
            maintenance_downtime = db.session.query(
                func.sum(OperatorLog.downtime_minutes)
            ).filter(
                OperatorLog.machine_id == machine.id,
                func.date(OperatorLog.created_at) == date,
                OperatorLog.downtime_category == 'maintenance'
            ).scalar() or 0
            
            # Total downtime is sum of unplanned and maintenance (planned downtime doesn't affect OEE)
            downtime = unplanned_downtime + maintenance_downtime
            
            # Available time excludes planned downtime
            available_time = total_time - planned_downtime
            
            # Calculate availability based on available time
            availability = (available_time - downtime) / available_time if available_time > 0 else 0
            
            # Calculate performance
            ideal_cycle_time = 1  # 1 minute per piece (standard)
            total_pieces = db.session.query(
                func.sum(OperatorLog.run_completed_quantity)
            ).filter(
                OperatorLog.machine_id == machine.id,
                func.date(OperatorLog.created_at) == date
            ).scalar() or 0
            
            actual_runtime = available_time - downtime
            performance = (ideal_cycle_time * total_pieces) / actual_runtime if actual_runtime > 0 else 0
            
            # Calculate quality
            good_pieces = db.session.query(
                func.sum(OperatorLog.run_completed_quantity)
            ).filter(
                OperatorLog.machine_id == machine.id,
                func.date(OperatorLog.created_at) == date,
                OperatorLog.quality_status == 'approved'
            ).scalar() or 0
            
            quality = good_pieces / total_pieces if total_pieces > 0 else 0
            
            # Calculate OEE
            oee = availability * performance * quality * 100
            total_oee += oee
        
        return round(total_oee / len(machines), 1) if machines else 0
    except Exception as e:
        app.logger.error(f"Error calculating daily OEE: {str(e)}")
        return 0

def calculate_quality_metrics():
    """Calculate overall quality metrics"""
    try:
        good_parts = db.session.query(
            func.sum(OperatorLog.run_completed_quantity)
        ).filter(
            OperatorLog.quality_status == 'approved'
        ).scalar() or 0
        
        rejected = db.session.query(
            func.sum(OperatorLog.run_rejected_quantity_fpi + OperatorLog.run_rejected_quantity_lpi)
        ).scalar() or 0
        
        rework = db.session.query(
            func.sum(OperatorLog.run_rework_quantity_fpi + OperatorLog.run_rework_quantity_lpi)
        ).scalar() or 0
        
        return {
            'good_parts': good_parts,
            'rejected': rejected,
            'rework': rework
        }
    except Exception as e:
        app.logger.error(f"Error calculating quality metrics: {str(e)}")
        return {'good_parts': 0, 'rejected': 0, 'rework': 0}

def calculate_downtime_distribution():
    """Calculate downtime distribution"""
    try:
        today = datetime.now(timezone.utc).date()
        
        # Get all types of downtime
        planned = db.session.query(
            func.sum(OperatorLog.downtime_minutes)
        ).filter(
            func.date(OperatorLog.created_at) == today,
            OperatorLog.downtime_category == 'planned'
        ).scalar() or 0
        
        unplanned = db.session.query(
            func.sum(OperatorLog.downtime_minutes)
        ).filter(
            func.date(OperatorLog.created_at) == today,
            OperatorLog.downtime_category == 'unplanned'
        ).scalar() or 0
        
        maintenance = db.session.query(
            func.sum(OperatorLog.downtime_minutes)
        ).filter(
            func.date(OperatorLog.created_at) == today,
            OperatorLog.downtime_category == 'maintenance'
        ).scalar() or 0
        
        # Calculate setup time from actual setup durations
        setup = db.session.query(
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
            func.date(OperatorLog.created_at) == today
        ).scalar() or 0
        
        return {
            'planned': planned,
            'unplanned': unplanned,
            'setup': setup,
            'maintenance': maintenance
        }
    except Exception as e:
        app.logger.error(f"Error calculating downtime distribution: {str(e)}")
        return {'planned': 0, 'unplanned': 0, 'setup': 0, 'maintenance': 0}

def get_machine_current_status(machine_id):
    """Get current status of a machine"""
    try:
        latest_log = OperatorLog.query.filter_by(
            machine_id=machine_id
        ).order_by(
            OperatorLog.created_at.desc()
        ).first()
        
        if not latest_log:
            return 'offline'
        
        if latest_log.current_status in ['cycle_started', 'cycle_resumed']:
            return 'running'
        elif latest_log.current_status in ['setup_started', 'setup_resumed']:
            return 'setup'
        else:
            return 'stopped'
    except Exception as e:
        app.logger.error(f"Error getting machine status: {str(e)}")
        return 'offline'

def calculate_machine_oee(machine_id):
    """Calculate OEE for a specific machine"""
    try:
        today = datetime.now(timezone.utc).date()
        
        # Calculate availability
        total_time = 24 * 60  # minutes in a day
        downtime = db.session.query(
            func.sum(OperatorLog.downtime_minutes)
        ).filter(
            OperatorLog.machine_id == machine_id,
            func.date(OperatorLog.created_at) == today
        ).scalar() or 0
        
        availability = (total_time - downtime) / total_time
        
        # Calculate performance
        ideal_cycle_time = 1  # 1 minute per piece (standard)
        total_pieces = db.session.query(
            func.sum(OperatorLog.run_completed_quantity)
        ).filter(
            OperatorLog.machine_id == machine_id,
            func.date(OperatorLog.created_at) == today
        ).scalar() or 0
        
        actual_runtime = total_time - downtime
        performance = (ideal_cycle_time * total_pieces) / actual_runtime if actual_runtime > 0 else 0
        
        # Calculate quality
        good_pieces = db.session.query(
            func.sum(OperatorLog.run_completed_quantity)
        ).filter(
            OperatorLog.machine_id == machine_id,
            func.date(OperatorLog.created_at) == today,
            OperatorLog.quality_status == 'approved'
        ).scalar() or 0
        
        quality = good_pieces / total_pieces if total_pieces > 0 else 0
        
        # Calculate OEE
        return round(availability * performance * quality * 100, 1)
    except Exception as e:
        app.logger.error(f"Error calculating machine OEE: {str(e)}")
        return 0

@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    try:
        # Join room based on user role
        if current_user.is_planner:
            join_room('planner')
        elif current_user.is_plant_head:
            join_room('plant_head')
        elif current_user.is_manager:
            join_room('manager')
    except Exception as e:
        app.logger.error(f"Error in socket connection: {str(e)}")

def emit_dashboard_updates():
    """Emit dashboard updates to connected clients"""
    try:
        with app.app_context():
            today = datetime.now(timezone.utc).date()
            
            # Get machine data
            machines = Machine.query.all()
            machine_names = [machine.name for machine in machines]
            
            # Calculate machine status counts
            machine_running = []
            machine_setup = []
            machine_stopped = []
            
            for machine in machines:
                current_status = get_machine_current_status(machine.id)
                machine_running.append(1 if current_status == 'running' else 0)
                machine_setup.append(1 if current_status == 'setup' else 0)
                machine_stopped.append(1 if current_status in ['stopped', 'offline'] else 0)
            
            # Calculate active machines
            active_machines_count = sum(machine_running) + sum(machine_setup)
            total_machines_count = len(machines)
            
            # Calculate machine utilization
            machine_utilization = round((active_machines_count / total_machines_count * 100), 1) if total_machines_count > 0 else 0
            
            # Calculate OEE trend
            timeline_dates = []
            oee_trend = []
            total_oee = 0
            
            for i in range(30):
                date = (today - timedelta(days=29-i))
                timeline_dates.append(date.strftime('%Y-%m-%d'))
                
                daily_oee = calculate_daily_oee(date)
                oee_trend.append(daily_oee)
                total_oee += daily_oee
            
            average_oee = round(total_oee / 30, 1)
            
            # Get quality metrics
            quality_metrics = calculate_quality_metrics()
            socketio.emit('quality_metrics_update', {'data': quality_metrics})
            
            # Get downtime data
            downtime = calculate_downtime_distribution()
            socketio.emit('downtime_update', {'data': downtime})
            
            # Get today's production count
            todays_production_count = db.session.query(
                func.sum(OperatorLog.run_completed_quantity)
            ).filter(
                func.date(OperatorLog.created_at) == today,
                OperatorLog.quality_status == 'approved'
            ).scalar() or 0

            # Get shift production data
            shifts = ['Morning', 'Afternoon', 'Night']
            shift_data = []
            
            for shift in shifts:
                production = db.session.query(func.sum(OperatorLog.run_completed_quantity))\
                    .filter(
                        func.date(OperatorLog.created_at) == today,
                        OperatorLog.shift == shift,
                        OperatorLog.run_completed_quantity > 0
                    ).scalar() or 0
                shift_data.append(int(production))

            # Calculate OEE for each machine
            oee_data = []
            for machine in machines:
                availability = calculate_availability(machine)
                performance = calculate_performance(machine)
                quality = calculate_quality(machine)
                oee = availability * performance * quality
                oee_percent = round(oee * 100, 2)
                oee_data.append(oee_percent)

            # Get production summary
            production_summary = []
            for product in EndProduct.query.all():
                if not validate_production_data(product.sap_id, product.quantity):
                    continue
                    
                completed = db.session.query(func.coalesce(func.sum(OperatorLog.run_completed_quantity), 0))\
                    .filter(
                        OperatorLog.end_product_sap_id == product.sap_id,
                        OperatorLog.run_completed_quantity > 0
                    ).scalar() or 0
                    
                rejected = db.session.query(func.coalesce(func.sum(
                    OperatorLog.run_rejected_quantity_fpi + OperatorLog.run_rejected_quantity_lpi), 0))\
                    .filter(
                        OperatorLog.end_product_sap_id == product.sap_id,
                        OperatorLog.run_rejected_quantity_fpi >= 0,
                        OperatorLog.run_rejected_quantity_lpi >= 0
                    ).scalar() or 0
                    
                rework = db.session.query(func.coalesce(func.sum(
                    OperatorLog.run_rework_quantity_fpi + OperatorLog.run_rework_quantity_lpi), 0))\
                    .filter(
                        OperatorLog.end_product_sap_id == product.sap_id,
                        OperatorLog.run_rework_quantity_fpi >= 0,
                        OperatorLog.run_rework_quantity_lpi >= 0
                    ).scalar() or 0
                
                production_summary.append({
                    "Product": product.name,
                    "SAP ID": product.sap_id,
                    "Planned Qty": product.quantity,
                    "Completed Qty": completed,
                    "Rejected Qty": rejected,
                    "Rework Qty": rework,
                    "Completion %": round((completed / product.quantity * 100), 1) if product.quantity else 0
                })

            # Get recent quality checks
            recent_quality_checks = QualityCheck.query.order_by(QualityCheck.timestamp.desc()).limit(5).all()
            recent_quality_data = []
            for item in recent_quality_checks:
                recent_quality_data.append({
                    'project_code': item.operator_log_rel.drawing_rel.end_product_rel.name if item.operator_log_rel and item.operator_log_rel.drawing_rel and item.operator_log_rel.drawing_rel.end_product_rel else 'N/A',
                    'sap_id': item.operator_log_rel.drawing_rel.sap_id if item.operator_log_rel and item.operator_log_rel.drawing_rel else 'N/A',
                    'drawing_number': item.operator_log_rel.drawing_rel.drawing_number if item.operator_log_rel and item.operator_log_rel.drawing_rel else 'N/A',
                    'type': f"QC {item.result.capitalize()}",
                    'reason': item.rejection_reason if item.result == 'reject' or item.result == 'rework' else '-',
                    'timestamp': item.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                    'by_whom': item.inspector_name
                })

            # Calculate pending quality checks
            pending_quality_checks = QualityCheck.query.filter_by(result='pending').count()

            # Emit updates to plant head dashboard
            socketio.emit('plant_head_update', {
                'oee_data': oee_data,
                'shift_data': shift_data,
                'machine_names': machine_names,
                'shift_labels': shifts,
                'production_summary': production_summary,
                'recent_quality_checks': recent_quality_data,
                'todays_production_count': todays_production_count,
                'pending_quality_checks': pending_quality_checks,
                'machine_utilization': machine_utilization,
                'average_oee': average_oee,
                'machine_status': {
                    'running': machine_running,
                    'setup': machine_setup,
                    'stopped': machine_stopped
                }
            }, room='plant_head')

            # Emit updates to planner dashboard
            # ... existing planner dashboard updates ...
            
    except Exception as e:
        app.logger.error(f"Error emitting dashboard updates: {str(e)}")

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection"""
    try:
        # Leave all rooms
        if current_user.is_planner:
            leave_room('planner')
        elif current_user.is_plant_head:
            leave_room('plant_head')
        elif current_user.is_manager:
            leave_room('manager')
    except Exception as e:
        app.logger.error(f"Error in socket disconnection: {str(e)}")

# Schedule dashboard updates every minute
scheduler = BackgroundScheduler(
    timezone='UTC',
    job_defaults={
        'coalesce': True,  # Only run once if multiple executions are missed
        'max_instances': 1,  # Only allow one instance of each job to run at a time
        'misfire_grace_time': 30  # Allow jobs to run up to 30 seconds late
    }
)

# Add jobs with proper error handling
def safe_emit_dashboard_updates():
    try:
        emit_dashboard_updates()
    except Exception as e:
        app.logger.error(f"Error in dashboard updates: {str(e)}")
        # Attempt recovery
        try:
            db.session.rollback()
        except:
            pass

def safe_emit_production_update():
    try:
        emit_production_update()
    except Exception as e:
        app.logger.error(f"Error in production update: {str(e)}")
        try:
            db.session.rollback()
        except:
            pass

# Add jobs with proper intervals
scheduler.add_job(safe_emit_dashboard_updates, 'interval', minutes=1, id='dashboard_updates')
scheduler.add_job(safe_emit_production_update, 'interval', seconds=30, id='production_updates')

# Start scheduler
try:
    scheduler.start()
    app.logger.info('Background scheduler started successfully')
except Exception as e:
    app.logger.error(f'Failed to start scheduler: {str(e)}')

# Ensure scheduler is shut down properly
atexit.register(lambda: scheduler.shutdown())

@app.route('/upload_drawing_mapping', methods=['POST'])
@login_required
def upload_drawing_mapping():
    """Handle drawing number and SAP ID mapping upload"""
    if not current_user.is_manager:
        app.logger.warning(f"Access denied for user {current_user.id} - Manager access required")
        flash('Access denied. Manager access required.', 'danger')
        return redirect(url_for('login_general'))

    try:
        app.logger.info("Starting drawing mapping upload process")
        
        if 'drawing_mapping_file' not in request.files:
            app.logger.warning("No file uploaded in request")
            flash('No file uploaded.', 'danger')
            return redirect(url_for('manager_dashboard'))

        file = request.files['drawing_mapping_file']
        if file.filename == '':
            app.logger.warning("Empty filename in uploaded file")
            flash('No file selected.', 'danger')
            return redirect(url_for('manager_dashboard'))

        if not file.filename.endswith('.xlsx'):
            app.logger.warning(f"Invalid file format: {file.filename}")
            flash('Invalid file format. Please upload an Excel (.xlsx) file.', 'danger')
            return redirect(url_for('manager_dashboard'))

        app.logger.info(f"Processing file: {file.filename}")
        
        # Read Excel file
        df = pd.read_excel(file)
        df.columns = [col.strip() for col in df.columns]  # Strip spaces from column names
        app.logger.info(f"Excel columns detected: {list(df.columns)}")

        # Validate required columns
        required_columns = ['drawing_number', 'sap_id']
        missing_columns = [col for col in required_columns if col not in df.columns]
        if missing_columns:
            app.logger.warning(f"Missing required columns: {missing_columns}")
            flash(f'Missing required columns: {", ".join(missing_columns)}', 'danger')
            return redirect(url_for('manager_dashboard'))

        # Process each row
        success_count = 0
        error_count = 0
        error_messages = []

        for index, row in df.iterrows():
            try:
                drawing_number = str(row['drawing_number']).strip()
                sap_id = str(row['sap_id']).strip()

                # Validate data
                if not drawing_number or not sap_id:
                    error_count += 1
                    error_messages.append(f'Row {index + 2}: Empty drawing number or SAP ID')
                    continue

                # Check if drawing number exists
                drawing = MachineDrawing.query.filter_by(drawing_number=drawing_number).first()
                if not drawing:
                    # Create new drawing
                    drawing = MachineDrawing(
                        drawing_number=drawing_number,
                        sap_id=sap_id
                    )
                    db.session.add(drawing)
                    app.logger.info(f"Created new drawing: {drawing_number}")
                else:
                    # Update existing drawing
                    drawing.sap_id = sap_id
                    app.logger.info(f"Updated existing drawing: {drawing_number}")

                success_count += 1

            except Exception as e:
                error_count += 1
                error_messages.append(f'Row {index + 2}: {str(e)}')
                app.logger.error(f"Error processing row {index + 2}: {str(e)}")

        # Commit changes
        db.session.commit()
        app.logger.info(f"Successfully processed {success_count} drawings with {error_count} errors")

        # Show results
        if success_count > 0:
            flash(f'Successfully processed {success_count} drawing(s).', 'success')
        if error_count > 0:
            flash(f'Failed to process {error_count} drawing(s). Errors: {"; ".join(error_messages)}', 'warning')

        return redirect(url_for('manager_dashboard'))

    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error uploading drawing mapping: {str(e)}")
        flash('An error occurred while processing the file.', 'danger')
        return redirect(url_for('manager_dashboard'))

def track_downtime(operator_log, downtime_category, minutes=None):
    """Track downtime for a machine
    
    Args:
        operator_log: The OperatorLog instance
        downtime_category: The category of downtime ('planned', 'unplanned', 'maintenance')
        minutes: Optional minutes to add. If not provided, will calculate from last status change
    """
    try:
        if minutes is None and operator_log.last_cycle_end_time:
            # Calculate minutes since last cycle end
            minutes = (datetime.now(timezone.utc) - operator_log.last_cycle_end_time).total_seconds() / 60
            
        if minutes and minutes > 0:
            operator_log.downtime_minutes = (operator_log.downtime_minutes or 0) + minutes
            operator_log.downtime_category = downtime_category
            db.session.commit()
            
    except Exception as e:
        app.logger.error(f"Error tracking downtime: {str(e)}")

@app.route('/machine_shop')
@login_required
def machine_shop():
    """Machine shop dashboard for managers"""
    if not current_user.is_manager:
        flash('Access denied. Only managers can access the machine shop.', 'danger')
        return redirect(url_for('login_general'))

    # Get all machines
    machines = Machine.query.order_by(Machine.name).all()
    
    # Get all assignments
    assignments = MachineDrawingAssignment.query.order_by(
        MachineDrawingAssignment.assigned_at.desc()
    ).all()
    
    # Calculate statistics
    total_assignments = len(assignments)
    running_jobs = sum(1 for a in assignments if a.status == 'running')
    pending_assignments = sum(1 for a in assignments if a.status == 'assigned')
    completed_today = sum(1 for a in assignments 
                         if a.status == 'completed' 
                         and a.updated_at.date() == datetime.now(timezone.utc).date())

    # Attach total_assigned to each assignment for template use
    for assignment in assignments:
        drawing = assignment.drawing_rel
        if drawing and hasattr(drawing, 'assignments'):
            total_assigned = sum(a.assigned_quantity for a in drawing.assignments)
            assignment.total_assigned = total_assigned
        else:
            assignment.total_assigned = 0

    # In machine_shop route, for each assignment, set current_operator:
    for assignment in assignments:
        active_session = OperatorSession.query.filter_by(machine_id=assignment.machine_id, is_active=True).first()
        if active_session:
            active_log = OperatorLog.query.filter_by(
                operator_session_id=active_session.id,
                drawing_id=assignment.drawing_id,
                current_status='cycle_started'
            ).first()  # REMOVED assignment_id=assignment.id
            if active_log:
                assignment.current_operator = active_session.operator_name
            else:
                assignment.current_operator = 'Not Assigned'
        else:
            assignment.current_operator = 'Not Assigned'

    return render_template('machine_shop.html',
                         machines=machines,
                         assignments=assignments,
                         total_assignments=total_assignments,
                         running_jobs=running_jobs,
                         pending_assignments=pending_assignments,
                         completed_today=completed_today)

@app.route('/machine_shop/assign', methods=['POST'])
@login_required
def machine_shop_assign():
    """Assign a drawing to a machine"""
    if not current_user.is_manager:
        return jsonify({'error': 'Access denied'}), 403

    try:
        drawing_number = request.form.get('drawing_number')
        machine_id = request.form.get('machine_id')
        quantity = int(request.form.get('quantity'))
        tool_number = request.form.get('tool_number')

        app.logger.info(f"Attempting to assign drawing. Input: drawing_number={drawing_number}, machine_id={machine_id}, quantity={quantity}, tool={tool_number}")

        # Validate drawing
        drawing = MachineDrawing.query.filter_by(drawing_number=drawing_number).first()
        if drawing:
            app.logger.info(f"Found drawing by drawing_number: {drawing.drawing_number}")
        else:
            app.logger.info(f"Drawing not found by drawing_number, trying SAP ID")
            # Try by SAP ID as well
            drawing = MachineDrawing.query.filter_by(sap_id=drawing_number).first()
            if drawing:
                app.logger.info(f"Found drawing by SAP ID: {drawing.sap_id}")
            else:
                app.logger.warning(f"Drawing not found by either drawing_number or SAP ID: {drawing_number}")
                flash('Drawing not found', 'danger')
                return redirect(url_for('machine_shop'))

        # Validate machine
        machine = Machine.query.get(machine_id)
        if not machine:
            flash('Machine not found', 'danger')
            return redirect(url_for('machine_shop'))

        # Check if drawing is already assigned to this machine
        existing = MachineDrawingAssignment.query.filter_by(
            drawing_id=drawing.id,
            machine_id=machine.id,
            status='assigned'
        ).first()
        if existing:
            flash('Drawing already assigned to this machine', 'warning')
            return redirect(url_for('machine_shop'))

        # Block if there is an active assignment for this machine/drawing
        existing = MachineDrawingAssignment.query.filter(
            MachineDrawingAssignment.drawing_id == drawing.id,
            MachineDrawingAssignment.machine_id == machine.id,
            MachineDrawingAssignment.status.in_(['assigned', 'running'])
        ).first()
        if existing:
            flash('Drawing already assigned to this machine (active assignment exists)', 'warning')
            return redirect(url_for('machine_shop'))

        # Block if total assigned quantity would exceed planned quantity
        total_assigned = db.session.query(
            func.sum(MachineDrawingAssignment.assigned_quantity)
        ).filter(
            MachineDrawingAssignment.drawing_id == drawing.id,
            MachineDrawingAssignment.status.in_(['assigned', 'running'])
        ).scalar() or 0
        planned_quantity = drawing.end_product_rel.quantity if drawing.end_product_rel else 0
        if total_assigned + quantity > planned_quantity:
            flash('Total assigned quantity would exceed planned quantity!', 'danger')
            return redirect(url_for('machine_shop'))

        # Create new assignment
        assignment = MachineDrawingAssignment(
            drawing_id=drawing.id,
            machine_id=machine.id,
            assigned_quantity=quantity,
            tool_number=tool_number,
            assigned_by=current_user.id
        )
        db.session.add(assignment)
        db.session.commit()

        flash('Drawing assigned successfully', 'success')
        return redirect(url_for('machine_shop'))

    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error assigning drawing: {str(e)}")
        flash('Error assigning drawing', 'danger')
        return redirect(url_for('machine_shop'))

@app.route('/machine_shop/transfer', methods=['POST'])
@login_required
def machine_shop_transfer():
    """Transfer a drawing assignment between machines"""
    if not current_user.is_manager:
        return jsonify({'error': 'Access denied'}), 403

    try:
        assignment_id = request.form.get('assignment_id')
        to_machine_id = request.form.get('to_machine_id')
        quantity = int(request.form.get('quantity'))
        reason = request.form.get('reason')

        # Get the assignment
        assignment = MachineDrawingAssignment.query.get(assignment_id)
        if not assignment:
            flash('Assignment not found', 'danger')
            return redirect(url_for('machine_shop'))

        # Validate target machine
        to_machine = Machine.query.get(to_machine_id)
        if not to_machine:
            flash('Target machine not found', 'danger')
            return redirect(url_for('machine_shop'))

        # Validate quantity
        if quantity > assignment.assigned_quantity:
            flash('Transfer quantity cannot exceed assigned quantity', 'danger')
            return redirect(url_for('machine_shop'))

        # Create transfer history
        transfer = TransferHistory(
            assignment_id=assignment.id,
            from_machine_id=assignment.machine_id,
            to_machine_id=to_machine.id,
            quantity_transferred=quantity,
            transferred_by=current_user.id,
            reason=reason
        )
        db.session.add(transfer)

        # Update original assignment
        assignment.assigned_quantity -= quantity
        if assignment.assigned_quantity == 0:
            assignment.status = 'transferred'

        # Create new assignment for target machine
        new_assignment = MachineDrawingAssignment(
            drawing_id=assignment.drawing_id,
            machine_id=to_machine.id,
            assigned_quantity=quantity,
            tool_number=assignment.tool_number,
            assigned_by=current_user.id
        )
        db.session.add(new_assignment)
        db.session.commit()

        flash('Drawing transferred successfully', 'success')
        return redirect(url_for('machine_shop'))

    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error transferring drawing: {str(e)}")
        flash('Error transferring drawing', 'danger')
        return redirect(url_for('machine_shop'))

@app.route('/machine_shop/history/<int:assignment_id>')
@login_required
def machine_shop_history(assignment_id):
    """Get history for a drawing assignment"""
    if not current_user.is_manager:
        return jsonify({'error': 'Access denied'}), 403

    try:
        assignment = MachineDrawingAssignment.query.get(assignment_id)
        if not assignment:
            return jsonify({'error': 'Assignment not found'}), 404

        history = []
        
        # Add initial assignment
        history.append({
            'timestamp': assignment.assigned_at.isoformat(),
            'action': 'Assigned',
            'from': None,
            'to': assignment.machine_rel.name,
            'quantity': assignment.assigned_quantity,
            'by': assignment.assigned_by_rel.username,
            'reason': None
        })

        # Add transfers
        for transfer in assignment.transfer_history:
            history.append({
                'timestamp': transfer.transferred_at.isoformat(),
                'action': 'Transferred',
                'from': transfer.from_machine_rel.name,
                'to': transfer.to_machine_rel.name,
                'quantity': transfer.quantity_transferred,
                'by': transfer.transferred_by_rel.username,
                'reason': transfer.reason
            })

        return jsonify({'history': history})

    except Exception as e:
        app.logger.error(f"Error getting assignment history: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/api/drawing_info/<drawing_number_or_sap_id>')
@login_required
def get_drawing_info(drawing_number_or_sap_id):
    try:
        app.logger.info(f"Drawing info requested for: {drawing_number_or_sap_id}")
        drawing = MachineDrawing.query.filter_by(drawing_number=drawing_number_or_sap_id).first()
        if not drawing:
            drawing = MachineDrawing.query.filter_by(sap_id=drawing_number_or_sap_id).first()
        if not drawing:
            app.logger.warning(f"Drawing or SAP ID not found: {drawing_number_or_sap_id}")
            return jsonify({'error': 'Drawing not found'}), 404
        total_assigned = db.session.query(
            func.sum(MachineDrawingAssignment.assigned_quantity)
        ).filter(
            MachineDrawingAssignment.drawing_id == drawing.id,
            MachineDrawingAssignment.status.in_(['assigned', 'running'])
        ).scalar() or 0
        total_quantity = drawing.end_product_rel.quantity if drawing.end_product_rel else 0
        remaining_quantity = total_quantity - total_assigned
        recent_assignment = MachineDrawingAssignment.query.filter_by(
            drawing_id=drawing.id
        ).order_by(MachineDrawingAssignment.assigned_at.desc()).first()
        recent_tool = recent_assignment.tool_number if recent_assignment else ''
        app.logger.info(f"Returning info: total={total_quantity}, assigned={total_assigned}, remaining={remaining_quantity}, tool={recent_tool}")
        return jsonify({
            'drawing_number': drawing.drawing_number,
            'sap_id': drawing.sap_id,
            'total_quantity': total_quantity,
            'assigned_quantity': total_assigned,
            'remaining_quantity': remaining_quantity,
            'recent_tool': recent_tool
        })
    except Exception as e:
        app.logger.error(f"Error getting drawing info: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

# Add new API endpoint for operator assignment info
@app.route('/api/operator_assignment_info/<drawing_number>/<machine_id>')
def api_operator_assignment_info(drawing_number, machine_id):
    try:
        drawing = MachineDrawing.query.filter_by(drawing_number=drawing_number).first()
        if not drawing:
            return jsonify({'error': 'Drawing not found'}), 404
        assignment = MachineDrawingAssignment.query.filter_by(
            drawing_id=drawing.id,
            machine_id=machine_id,
            status='assigned'
        ).first()
        if not assignment:
            return jsonify({'error': 'Assignment not found'}), 404
        completed = db.session.query(func.sum(OperatorLog.run_completed_quantity)).filter(
            OperatorLog.machine_id == assignment.machine_id,
            OperatorLog.drawing_id == assignment.drawing_id,
            OperatorLog.quality_status == 'approved'
        ).scalar() or 0
        return jsonify({
            'drawing_number': drawing.drawing_number,
            'tool_number': assignment.tool_number,
            'assigned_quantity': assignment.assigned_quantity,
            'completed_quantity': completed,
            'status': assignment.status
        })
    except Exception as e:
        app.logger.error(f"Error in api_operator_assignment_info: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

# Utility to convert UTC to IST
IST = pytz_timezone('Asia/Kolkata')
def utc_to_ist(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST)

if __name__ == '__main__':
    try:
        # Initialize the application
        initialize_application()
        
        # Start the update scheduler in a background thread
        scheduler_thread = threading.Thread(target=schedule_updates, daemon=True)
        scheduler_thread.start()
        app.logger.info("Update scheduler started successfully")  # Add logging
        
        # Run the application
        socketio.run(app, debug=True, use_reloader=False)
    except Exception as e:
        app.logger.error(f"Error starting application: {str(e)}")
        app.logger.error(f"Stack trace: {traceback.format_exc()}")


