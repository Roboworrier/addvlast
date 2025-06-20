from flask import Blueprint, request, jsonify, session, render_template, redirect, url_for, flash
from flask_login import login_required, current_user
from datetime import datetime, timezone
from models import db, OperatorLog, OperatorSession, Machine, OperatorLogAction

operator_log_bp = Blueprint('operator_log', __name__, url_prefix='/operator_log')

@operator_log_bp.route('/handover_prompt/<int:machine_id>', methods=['GET'])
@login_required
def handover_prompt(machine_id):
    # Find open logs for this machine
    open_logs = OperatorLog.query.filter(
        OperatorLog.machine_id == machine_id,
        OperatorLog.current_status.in_([
            'setup_started', 'setup_done', 'cycle_started', 'cycle_paused',
            'fpi_passed_ready_for_cycle', 'cycle_completed', 'cycle_completed_pending_fpi', 'lpi_pending'
        ])
    ).all()
    return render_template('handover_prompt.html', open_logs=open_logs, machine_id=machine_id)

@operator_log_bp.route('/close_log/<int:log_id>', methods=['POST'])
@login_required
def close_log(log_id):
    log = OperatorLog.query.get_or_404(log_id)
    # Only allow manager or supervisor to close
    if not (current_user.is_manager or current_user.is_admin):
        flash('Only a manager or admin can close logs.', 'danger')
        return redirect(url_for('operator_log.handover_prompt', machine_id=log.machine_id))
    log.current_status = 'admin_closed'
    log.closed_by = current_user.username
    log.closed_at = datetime.now(timezone.utc)
    db.session.commit()
    flash(f'Log {log_id} closed by {current_user.username}.', 'success')
    return redirect(url_for('operator_log.handover_prompt', machine_id=log.machine_id))

@operator_log_bp.route('/record_action', methods=['POST'])
@login_required
def record_action():
    data = request.json
    log_id = data.get('log_id')
    action_type = data.get('action_type')
    quantity = data.get('quantity')
    shift = data.get('shift')
    log_action = OperatorLogAction(
        log_id=log_id,
        operator_name=current_user.username,
        shift=shift,
        action_type=action_type,
        quantity=quantity,
        timestamp=datetime.now(timezone.utc)
    )
    db.session.add(log_action)
    db.session.commit()
    return jsonify({'status': 'success'})

@operator_log_bp.route('/open_logs', methods=['GET'])
@login_required
def get_open_logs():
    if not (current_user.is_manager or current_user.is_admin):
        return jsonify({'logs': []})
    open_logs = OperatorLog.query.filter(
        OperatorLog.current_status.in_([
            'setup_started', 'setup_done', 'cycle_started', 'cycle_paused',
            'fpi_passed_ready_for_cycle', 'cycle_completed', 'cycle_completed_pending_fpi', 'lpi_pending'
        ])
    ).all()
    logs = []
    for log in open_logs:
        logs.append({
            'id': log.id,
            'machine': Machine.query.get(log.machine_id).name if log.machine_id else 'Unknown',
            'drawing': log.drawing_number,
            'operator': log.operator_session.operator_name if log.operator_session else '',
            'shift': log.shift or (log.operator_session.shift if log.operator_session else ''),
            'status': log.current_status,
            'created_at': log.created_at.strftime('%Y-%m-%d %H:%M:%S'),
        })
    return jsonify({'logs': logs}) 