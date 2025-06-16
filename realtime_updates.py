"""
Real-time updates module for ChipSight
"""
from datetime import datetime, timezone
from flask import current_app
from flask_socketio import emit
import eventlet
from collections import OrderedDict
import threading
import json
from user_management import can_add_user, add_user_session, remove_user_session, update_session_activity

# Real-time update functions

def emit_live_dpr_update(db, socketio):
    """Emit real-time DPR data updates"""
    try:
        from models import EndProduct, OperatorLog
        
        # Get current production data
        production_data = db.session.query(
            EndProduct.name,
            EndProduct.sap_id,
            EndProduct.quantity,
            db.func.coalesce(db.func.sum(OperatorLog.run_completed_quantity), 0).label('completed'),
            db.func.coalesce(db.func.sum(OperatorLog.run_rejected_quantity_fpi + OperatorLog.run_rejected_quantity_lpi), 0).label('rejected'),
            db.func.coalesce(db.func.sum(OperatorLog.run_rework_quantity_fpi + OperatorLog.run_rework_quantity_lpi), 0).label('rework')
        ).outerjoin(
            OperatorLog, OperatorLog.end_product_sap_id == EndProduct.sap_id
        ).group_by(EndProduct.id).all()

        # Format data for DPR
        dpr_data = [{
            'name': str(name),
            'sap_id': str(sap_id),
            'planned': int(quantity),
            'completed': int(completed),
            'rejected': int(rejected),
            'rework': int(rework),
            'completion_rate': round((completed / quantity * 100 if quantity > 0 else 0), 2),
            'quality_rate': round(((completed - rejected - rework) / completed * 100 if completed > 0 else 0), 2),
            'timestamp': datetime.now(timezone.utc).isoformat()
        } for name, sap_id, quantity, completed, rejected, rework in production_data]

        socketio.emit('dpr_update', {'data': dpr_data})
        
    except Exception as e:
        current_app.logger.error(f"Error emitting DPR update: {str(e)}")

def emit_machine_metrics(db, socketio):
    """Emit real-time machine metrics"""
    try:
        from models import Machine, OperatorSession, OperatorLog
        
        machines = Machine.query.all()
        metrics = []
        
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
            metrics.append({
                'machine_name': machine.name,
                'status': machine.status,
                'operator': active_session.operator_name if active_session else None,
                'current_job': current_log.drawing_rel.drawing_number if current_log and current_log.drawing_rel else None,
                'parts_completed': current_log.run_completed_quantity if current_log else 0,
                'parts_rejected': (current_log.run_rejected_quantity_fpi + current_log.run_rejected_quantity_lpi) if current_log else 0,
                'timestamp': datetime.now(timezone.utc).isoformat()
            })

        socketio.emit('machine_metrics', {'data': metrics})
        
    except Exception as e:
        current_app.logger.error(f"Error emitting machine metrics: {str(e)}")

def schedule_updates(app, db, socketio):
    """Schedule all real-time updates"""
    while True:
        try:
            with app.app_context():
                emit_live_dpr_update(db, socketio)
                emit_machine_metrics(db, socketio)
                db.session.remove()  # Clean up session
        except Exception as e:
            current_app.logger.error(f"Error in update scheduler: {str(e)}")
        eventlet.sleep(30)  # Update every 30 seconds

def init_realtime(app, db, socketio):
    """Initialize real-time updates"""
    eventlet.spawn(schedule_updates, app, db, socketio) 