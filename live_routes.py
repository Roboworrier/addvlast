from flask import Blueprint, Response, flash, redirect, url_for, current_app, request
from flask_login import login_required, current_user
from datetime import datetime
from models import db
from live_data import get_live_production_data, generate_dpr_csv, get_machine_metrics
from user_management import can_add_user, add_user_session, remove_user_session, update_session_activity

live_bp = Blueprint('live', __name__)

@live_bp.route('/live_dpr/download')
@login_required
def download_live_dpr():
    try:
        # Get current production data
        production_data = get_live_production_data(db)
        if not production_data:
            flash('No production data available', 'warning')
            return redirect(url_for('planner_dashboard'))
            
        # Generate CSV
        csv_data = generate_dpr_csv(production_data)
        if not csv_data:
            flash('Error generating report', 'danger')
            return redirect(url_for('planner_dashboard'))
            
        # Create response
        return Response(
            csv_data,
            mimetype='text/csv',
            headers={
                'Content-Disposition': f'attachment;filename=live_dpr_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
            }
        )
    except Exception as e:
        current_app.logger.error(f"Error downloading live DPR: {str(e)}")
        flash('Error generating report', 'danger')
        return redirect(url_for('planner_dashboard'))

def init_live_routes(app, socketio):
    """Initialize live routes and Socket.IO events"""
    
    @socketio.on('connect')
    def handle_connect():
        if not current_user.is_authenticated:
            return False
            
        if not can_add_user():
            return False
            
        add_user_session(request.sid, current_user.get_id())
        return True
        
    @socketio.on('disconnect')
    def handle_disconnect():
        remove_user_session(request.sid)
        
    @socketio.on('request_update')
    def handle_update_request():
        try:
            production_data = get_live_production_data(db)
            machine_metrics = get_machine_metrics(db)
            
            socketio.emit('dpr_update', {'data': production_data})
            socketio.emit('machine_metrics', {'data': machine_metrics})
        except Exception as e:
            current_app.logger.error(f"Error handling update request: {str(e)}")
    
    app.register_blueprint(live_bp) 