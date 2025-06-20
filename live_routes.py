from flask import Blueprint, Response, flash, redirect, url_for, current_app, request, jsonify, render_template
from flask_login import login_required, current_user
from datetime import datetime, timezone
from models import db, MachineDrawing, ProcessRoute, RouteOperation, OperatorLog, QualityCheck, LPIRecord, MachineDrawingAssignment, Machine, ShippingRecord, TransferHistory, ReworkQueue
from sqlalchemy import desc, func, and_
from live_data import get_live_production_data, generate_dpr_csv, get_machine_metrics, generate_detailed_dpr_csv
from user_management import can_add_user, add_user_session, remove_user_session, update_session_activity
import os
from datetime import timedelta
import re

# Create blueprint with unique name
live_bp = Blueprint('live_routes', __name__, url_prefix='/live')

@live_bp.route('/dpr/download', methods=['GET'])
@login_required
def download_live_dpr():
    try:
        # Get date from query param, default to today
        date_str = request.args.get('date')
        if date_str:
            try:
                report_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            except Exception:
                flash('Invalid date format. Use YYYY-MM-DD.', 'danger')
                return redirect(url_for('planner_dashboard'))
        else:
            now = datetime.now(timezone.utc)
            report_date = now.date()

        # Ensure dpr_reports directory exists
        reports_dir = os.path.join(os.getcwd(), 'dpr_reports')
        os.makedirs(reports_dir, exist_ok=True)
        report_filename = f'dpr_{report_date.strftime("%Y%m%d")}.csv'
        report_path = os.path.join(reports_dir, report_filename)

        # If file exists, serve it
        if os.path.exists(report_path):
            return Response(
                open(report_path, 'rb'),
                mimetype='text/csv',
                headers={
                    'Content-Disposition': f'attachment;filename={report_filename}'
                }
            )

        # Otherwise, generate from DB
        logs = db.session.query(OperatorLog).filter(
            func.date(OperatorLog.created_at) == report_date
        ).order_by(OperatorLog.created_at.desc()).all()
        formatted_logs = []
        for log in logs:
            machine = Machine.query.get(log.machine_id) if log.machine_id else None
            drawing = log.drawing_rel
            end_product = drawing.end_product_rel if drawing else None
            project = end_product.project_rel if end_product else None
            setup_time = (log.setup_end_time - log.setup_start_time).total_seconds() / 60 if log.setup_end_time and log.setup_start_time else None
            cycle_time = (log.last_cycle_end_time - log.first_cycle_start_time).total_seconds() / 60 if log.last_cycle_end_time and log.first_cycle_start_time else None
            setup_start = log.setup_start_time.strftime('%H:%M:%S') if log.setup_start_time else ''
            setup_end = log.setup_end_time.strftime('%H:%M:%S') if log.setup_end_time else ''
            cycle_start = log.first_cycle_start_time.strftime('%H:%M:%S') if log.first_cycle_start_time else ''
            cycle_end = log.last_cycle_end_time.strftime('%H:%M:%S') if log.last_cycle_end_time else ''
            formatted_logs.append({
                'date': log.created_at.strftime('%Y-%m-%d'),
                'mc_do': machine.name if machine else 'Unknown',
                'operator': log.operator_session.operator_name if log.operator_session else 'Unknown',
                'proj': project.project_code if project else 'Unknown',
                'sap_no': end_product.sap_id if end_product else 'Unknown',
                'drg_no': drawing.drawing_number if drawing else 'Unknown',
                'setup_start_utc': setup_start,
                'setup_end_utc': setup_end,
                'first_cycle_start_utc': cycle_start,
                'last_cycle_end_utc': cycle_end,
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
                'oee': log.oee
            })
        from live_data import generate_detailed_dpr_csv
        csv_data = generate_detailed_dpr_csv(formatted_logs)
        if not csv_data:
            flash('Error generating report', 'danger')
            return redirect(url_for('planner_dashboard'))
        # Save to file
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(csv_data)
        return Response(
            csv_data,
            mimetype='text/csv',
            headers={
                'Content-Disposition': f'attachment;filename={report_filename}'
            }
        )
    except Exception as e:
        current_app.logger.error(f"Error downloading live DPR: {str(e)}")
        flash('Error generating report', 'danger')
        return redirect(url_for('planner_dashboard'))

@live_bp.route('/operator/shipping/<machine_name>', methods=['GET'])
def operator_shipping(machine_name):
    machine = Machine.query.filter_by(name=machine_name).first_or_404()
    
    # Get all available machines for shipping destination
    available_machines = Machine.query.filter(Machine.name != machine_name).all()
    
    # Get assignments with completed parts ready for shipping
    assignment_rows = db.session.query(
        MachineDrawing.drawing_number,
        func.sum(MachineDrawingAssignment.completed_quantity).label('completed_quantity'),
        func.sum(MachineDrawingAssignment.shipped_quantity).label('shipped_quantity')
    ).join(MachineDrawing, MachineDrawingAssignment.drawing_id == MachineDrawing.id)
    assignment_rows = assignment_rows.filter(
        MachineDrawingAssignment.machine_id == machine.id,
        MachineDrawingAssignment.completed_quantity > 0
    ).group_by(
        MachineDrawing.drawing_number
    ).having(
        func.sum(MachineDrawingAssignment.completed_quantity) > func.sum(MachineDrawingAssignment.shipped_quantity)
    ).all()
    
    return render_template(
        'operator_shipping.html',
        machine=machine,
        available_machines=available_machines,
        assignment_rows=assignment_rows
    )

@live_bp.route('/ship-parts', methods=['POST'])
@login_required
def ship_parts():
    try:
        source_machine = request.form.get('machine_name')
        drawing_number = request.form.get('drawing_number')
        destination_machine = request.form.get('destination_machine')
        ship_quantity = int(request.form.get('ship_quantity', 0))
        shipping_notes = request.form.get('shipping_notes', '')
        batch_id = request.form.get('batch_id')
        batch_quantity = int(request.form.get('batch_quantity', 0))

        if not all([source_machine, drawing_number, destination_machine, ship_quantity, batch_id, batch_quantity]):
            flash('All fields are required', 'error')
            return redirect(url_for('live_routes.operator_shipping', machine_name=source_machine))

        # Get the assignment
        assignment = MachineDrawingAssignment.query.join(MachineDrawing).filter(
            MachineDrawing.drawing_number == drawing_number,
            MachineDrawingAssignment.machine_id == Machine.query.filter_by(name=source_machine).first().id
        ).first()

        if not assignment:
            flash('Drawing assignment not found', 'error')
            return redirect(url_for('live_routes.operator_shipping', machine_name=source_machine))

        # Check if enough completed parts are available in the batch
        available_quantity = assignment.completed_quantity - assignment.shipped_quantity
        if ship_quantity > available_quantity:
            flash(f'Only {available_quantity} parts available for shipping', 'error')
            return redirect(url_for('live_routes.operator_shipping', machine_name=source_machine))

        # Check if LPI is passed for this batch using OperatorLog
        operator_log = OperatorLog.query.filter_by(batch_id=batch_id).order_by(OperatorLog.created_at.desc()).first()
        if not operator_log or operator_log.lpi_status not in ['pass', 'passed', 'lpi_completed']:
            flash('LPI must be passed for this batch before shipping.', 'error')
            return redirect(url_for('live_routes.operator_shipping', machine_name=source_machine))

        # Create shipping record
        shipping_record = ShippingRecord(
            drawing_number=drawing_number,
            from_machine=source_machine,
            to_machine=destination_machine,
            quantity=ship_quantity,
            notes=shipping_notes,
            date=datetime.now()
        )
        db.session.add(shipping_record)

        # Update shipped quantity
        assignment.shipped_quantity = (assignment.shipped_quantity or 0) + ship_quantity
        db.session.commit()
        flash('Parts shipped successfully', 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'Error shipping parts: {str(e)}', 'error')
        
    return redirect(url_for('live_routes.operator_shipping', machine_name=source_machine))

def init_live_routes(app, socketio=None):
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
    
    # Register blueprint with unique name
    app.register_blueprint(live_bp)

@live_bp.route('/api/part-finder/search')
def search_part():
    query = request.args.get('query', '').strip()
    if not query:
        return jsonify({'status': 'error', 'message': 'Search query is required'})

    try:
        # Search by SAP ID or Drawing Number
        drawing = MachineDrawing.query.filter(
            (MachineDrawing.sap_id == query) | (MachineDrawing.drawing_number == query)
        ).first()

        if not drawing:
            return jsonify({'status': 'success', 'part': None})

        # Get the latest route for this drawing
        current_route = ProcessRoute.query.filter_by(
            sap_id=drawing.sap_id
        ).order_by(ProcessRoute.created_at.desc()).first()

        # Calculate quantities
        total_quantity = drawing.end_product_rel.quantity if drawing.end_product_rel else 0
        completed_quantity = 0
        in_progress_quantity = 0

        route_data = []
        checkpoints = []
        if current_route:
            operations = RouteOperation.query.filter_by(
                route_id=current_route.id
            ).order_by(RouteOperation.sequence).all()

            for op in operations:
                # Get all operator logs for this drawing and operation type
                logs = OperatorLog.query.filter_by(
                    drawing_id=drawing.id,
                    current_status=op.operation_type
                ).order_by(OperatorLog.created_at.desc()).all()

                # Get latest assignment for this operation
                latest_assignment = MachineDrawingAssignment.query.filter_by(
                    drawing_id=drawing.id,
                    machine_id=Machine.query.filter_by(name=op.assigned_machine).first().id if op.assigned_machine else None
                ).order_by(MachineDrawingAssignment.assigned_at.desc()).first()
                assigned_machine = latest_assignment.machine_rel.name if latest_assignment and latest_assignment.machine_rel else op.assigned_machine

                # Aggregate quantities and status
                op_completed = sum([log.run_completed_quantity or 0 for log in logs])
                op_in_progress = sum([(log.run_planned_quantity or 0) - (log.run_completed_quantity or 0) for log in logs])
                status = 'PENDING'
                if op_completed > 0:
                    status = 'COMPLETED'
                elif op_in_progress > 0:
                    status = 'IN_PROGRESS'

                # Get FPI/LPI QC info for this operation
                fpi_status = lpi_status = 'PENDING'
                for log in logs:
                    if log.fpi_status:
                        fpi_status = log.fpi_status.upper()
                    if log.lpi_status:
                        lpi_status = log.lpi_status.upper()

                # Check for transfer/rework events
                transfer_event = TransferHistory.query.filter_by(
                    assignment_id=latest_assignment.id if latest_assignment else None
                ).order_by(TransferHistory.transferred_at.desc()).first() if latest_assignment else None
                rework_event = ReworkQueue.query.filter_by(
                    operator_log_id=logs[0].id if logs else None
                ).order_by(ReworkQueue.created_at.desc()).first() if logs else None

                checkpoints.append({
                    'operation_type': op.operation_type,
                    'machine_name': assigned_machine,
                    'completed_quantity': op_completed,
                    'in_progress_quantity': op_in_progress,
                    'status': status.upper(),
                    'fpi_status': fpi_status,
                    'lpi_status': lpi_status,
                    'transfer': bool(transfer_event),
                    'rework': bool(rework_event)
                })

                completed_quantity += op_completed
                in_progress_quantity += op_in_progress

                route_data.append({
                    'id': op.id,
                    'machine_name': assigned_machine,
                    'operation_type': op.operation_type,
                    'status': status.upper(),
                    'completed_quantity': op_completed,
                    'in_progress_quantity': op_in_progress,
                })

        # Get QC information for this drawing
        qc_info = []
        quality_checks = QualityCheck.query.join(OperatorLog, QualityCheck.operator_log_id == OperatorLog.id).filter(
            OperatorLog.drawing_id == drawing.id
        ).order_by(QualityCheck.timestamp.desc()).all()
        for qc in quality_checks:
            qc_info.append({
                'id': qc.id,
                'check_type': qc.check_type,
                'result': qc.result,
                'inspector': qc.inspector_name,
                'date': qc.timestamp.isoformat(),
                'remarks': qc.rejection_reason
            })

        remaining_quantity = total_quantity - completed_quantity - in_progress_quantity

        part_data = {
            'sap_id': drawing.sap_id,
            'drawing_number': drawing.drawing_number,
            'status': 'COMPLETED' if completed_quantity == total_quantity else 'IN_PROGRESS',
            'total_quantity': total_quantity,
            'completed_quantity': completed_quantity,
            'in_progress_quantity': in_progress_quantity,
            'remaining_quantity': remaining_quantity,
            'route': route_data,
            'qc_info': qc_info,
            'checkpoints': checkpoints
        }

        return jsonify({
            'status': 'success',
            'part': part_data
        })

    except Exception as e:
        current_app.logger.error(f"Error in part finder: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': 'An error occurred while searching for the part'
        })

@live_bp.route('/dpr/list', methods=['GET'])
@login_required
def list_dpr_reports():
    reports_dir = os.path.join(os.getcwd(), 'dpr_reports')
    os.makedirs(reports_dir, exist_ok=True)
    files = os.listdir(reports_dir)
    # Match files like dpr_YYYYMMDD.csv
    pattern = re.compile(r'dpr_(\d{8})\.csv')
    available = []
    cutoff = datetime.now() - timedelta(days=30)
    for f in files:
        m = pattern.match(f)
        if m:
            date_str = m.group(1)
            try:
                file_date = datetime.strptime(date_str, '%Y%m%d')
                if file_date >= cutoff:
                    available.append({
                        'date': file_date.strftime('%Y-%m-%d'),
                        'filename': f
                    })
            except Exception:
                continue
    available.sort(key=lambda x: x['date'], reverse=True)
    return jsonify({'reports': available})

@live_bp.route('/dpr/data', methods=['GET'])
@login_required
def get_live_dpr_data():
    try:
        date_str = request.args.get('date')
        if date_str:
            try:
                query_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            except Exception:
                query_date = datetime.now(timezone.utc).date()
        else:
            query_date = datetime.now(timezone.utc).date()
        logs = db.session.query(OperatorLog).filter(
            func.date(OperatorLog.created_at) == query_date
        ).order_by(OperatorLog.created_at.desc()).all()
        formatted_logs = []
        for log in logs:
            machine = Machine.query.get(log.machine_id) if log.machine_id else None
            drawing = log.drawing_rel
            end_product = drawing.end_product_rel if drawing else None
            project = end_product.project_rel if end_product else None
            setup_time = (log.setup_end_time - log.setup_start_time).total_seconds() / 60 if log.setup_end_time and log.setup_start_time else None
            cycle_time = (log.last_cycle_end_time - log.first_cycle_start_time).total_seconds() / 60 if log.last_cycle_end_time and log.first_cycle_start_time else None
            setup_start = log.setup_start_time.strftime('%H:%M:%S') if log.setup_start_time else ''
            setup_end = log.setup_end_time.strftime('%H:%M:%S') if log.setup_end_time else ''
            cycle_start = log.first_cycle_start_time.strftime('%H:%M:%S') if log.first_cycle_start_time else ''
            cycle_end = log.last_cycle_end_time.strftime('%H:%M:%S') if log.last_cycle_end_time else ''
            formatted_logs.append({
                'date': log.created_at.strftime('%Y-%m-%d'),
                'mc_do': machine.name if machine else 'Unknown',
                'operator': log.operator_session.operator_name if log.operator_session else 'Unknown',
                'proj': project.project_code if project else 'Unknown',
                'sap_no': end_product.sap_id if end_product else 'Unknown',
                'drg_no': drawing.drawing_number if drawing else 'Unknown',
                'setup_start_utc': setup_start,
                'setup_end_utc': setup_end,
                'first_cycle_start_utc': cycle_start,
                'last_cycle_end_utc': cycle_end,
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
                'oee': log.oee
            })
        return jsonify({'logs': formatted_logs})
    except Exception as e:
        current_app.logger.error(f"Error fetching live DPR data: {str(e)}")
        return jsonify({'logs': []}) 