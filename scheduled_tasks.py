from datetime import datetime, timezone, timedelta
from flask import current_app
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from models import db, OperatorLog, Machine, OperatorSession
from live_data import get_live_production_data, get_machine_metrics
import atexit

scheduler = BackgroundScheduler()

def emit_dpr_updates(app, socketio):
    """Emit DPR updates to connected clients"""
    with app.app_context():
        try:
            current_app.logger.info("Starting DPR data emission...")
            
            # Get production data
            production_data = get_live_production_data(db)
            current_app.logger.info(f"Retrieved {len(production_data)} DPR records")
            
            # Get machine metrics
            machine_metrics = get_machine_metrics(db)
            current_app.logger.info(f"Retrieved {len(machine_metrics)} machine metrics")
            
            # Format data for emission
            formatted_data = {
                'production_data': production_data,
                'machine_metrics': machine_metrics,
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            current_app.logger.info(f"Formatted {len(production_data)} DPR records")
            
            # Emit updates
            socketio.emit('dpr_update', {'data': formatted_data})
            current_app.logger.info("DPR data emitted successfully")
            
            # Update machine statuses
            update_machine_statuses(app)
            
        except Exception as e:
            current_app.logger.error(f"Error in DPR update: {str(e)}")

def update_machine_statuses(app):
    """Update machine statuses based on activity"""
    with app.app_context():
        try:
            # Get all machines
            machines = Machine.query.all()
            now = datetime.now(timezone.utc)
            
            for machine in machines:
                # Get active session
                active_session = OperatorSession.query.filter_by(
                    machine_id=machine.id,
                    is_active=True
                ).first()
                
                # Get current log
                current_log = None
                if active_session:
                    current_log = OperatorLog.query.filter(
                        OperatorLog.operator_session_id == active_session.id,
                        OperatorLog.current_status.notin_(['cycle_completed', 'admin_closed'])
                    ).order_by(OperatorLog.created_at.desc()).first()
                
                # Update machine status
                new_status = determine_machine_status(machine, active_session, current_log, now)
                if new_status != machine.status:
                    machine.status = new_status
                    db.session.add(machine)
            
            db.session.commit()
            
        except Exception as e:
            current_app.logger.error(f"Error updating machine statuses: {str(e)}")
            db.session.rollback()

def determine_machine_status(machine, session, log, now):
    """Determine machine status based on session and log data"""
    if machine.status == 'breakdown':
        return 'breakdown'
        
    if not session:
        return 'idle'
        
    if not log:
        return 'idle'
        
    # Check for inactivity (30 minutes)
    last_activity = log.updated_at or log.created_at
    if last_activity.tzinfo is None:
        last_activity = last_activity.replace(tzinfo=timezone.utc)
    
    if (now - last_activity).total_seconds() > 1800:  # 30 minutes
        return 'idle'
        
    status_map = {
        'setup_started': 'setup',
        'cycle_started': 'running',
        'cycle_paused': 'paused',
        'setup_done': 'ready',
        'fpi_passed_ready_for_cycle': 'ready'
    }
    
    return status_map.get(log.current_status, 'idle')

def init_scheduled_tasks(app, socketio):
    """Initialize scheduled tasks"""
    try:
        # Schedule DPR updates every 30 seconds
        scheduler.add_job(
            func=emit_dpr_updates,
            args=[app, socketio],
            trigger=IntervalTrigger(seconds=30),
            id='dpr_update',
            name='DPR Update Job',
            replace_existing=True
        )
        
        # Start the scheduler
        if not scheduler.running:
            scheduler.start()
            current_app.logger.info("Scheduled tasks initialized successfully")
            
        # Register shutdown
        atexit.register(lambda: scheduler.shutdown(wait=False))
        
    except Exception as e:
        current_app.logger.error(f"Error initializing scheduled tasks: {str(e)}") 