from datetime import datetime, timezone, timedelta
from flask import current_app
import csv
from io import StringIO
from utils import validate_production_data

def get_live_production_data(db):
    """Get live production data with error handling for missing columns"""
    try:
        from models import EndProduct, OperatorLog
        
        # Get all end products
        products = EndProduct.query.all()
        production_data = []
        
        for product in products:
            # Get completed quantity with error handling
            completed = db.session.query(db.func.coalesce(db.func.sum(
                OperatorLog.run_completed_quantity), 0))\
                .filter(
                    OperatorLog.end_product_sap_id == product.sap_id,
                    OperatorLog.run_completed_quantity >= 0
                ).scalar() or 0
            
            # Get rejected quantity with error handling
            rejected = db.session.query(db.func.coalesce(db.func.sum(
                OperatorLog.run_rejected_quantity_fpi + OperatorLog.run_rejected_quantity_lpi), 0))\
                .filter(
                    OperatorLog.end_product_sap_id == product.sap_id,
                    OperatorLog.run_rejected_quantity_fpi >= 0,
                    OperatorLog.run_rejected_quantity_lpi >= 0
                ).scalar() or 0
            
            # Get rework quantity with error handling
            rework = db.session.query(db.func.coalesce(db.func.sum(
                OperatorLog.run_rework_quantity_fpi + OperatorLog.run_rework_quantity_lpi), 0))\
                .filter(
                    OperatorLog.end_product_sap_id == product.sap_id,
                    OperatorLog.run_rework_quantity_fpi >= 0,
                    OperatorLog.run_rework_quantity_lpi >= 0
                ).scalar() or 0
            
            # Calculate metrics safely
            completion_rate = round((completed / product.quantity * 100 if product.quantity > 0 else 0), 2)
            quality_rate = round(((completed - rejected - rework) / completed * 100 if completed > 0 else 0), 2)
            
            production_data.append({
                'name': str(product.name),
                'sap_id': str(product.sap_id),
                'planned': int(product.quantity),
                'completed': int(completed),
                'rejected': int(rejected),
                'rework': int(rework),
                'completion_rate': completion_rate,
                'quality_rate': quality_rate,
                'timestamp': datetime.now(timezone.utc).isoformat()
            })
            
        return production_data
    except Exception as e:
        current_app.logger.error(f"Error getting live production data: {str(e)}")
        return []

def calculate_efficiency(operator_log):
    """Calculate efficiency for an operator log with proper timezone handling"""
    try:
        if not operator_log:
            return 0
            
        # Ensure all timestamps are timezone-aware
        now = datetime.now(timezone.utc)
        start_time = operator_log.created_at
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
            
        # Calculate time difference
        time_diff = now - start_time
        hours_diff = time_diff.total_seconds() / 3600
        
        if hours_diff <= 0:
            return 0
            
        # Calculate parts per hour using run_planned_quantity instead of planned_quantity
        completed_qty = operator_log.run_completed_quantity or 0
        target_qty = operator_log.run_planned_quantity or 0  # Changed from planned_quantity
        
        if target_qty <= 0:
            return 0
            
        actual_rate = completed_qty / hours_diff if hours_diff > 0 else 0
        target_rate = target_qty / 8  # Assuming 8-hour shift
        
        efficiency = (actual_rate / target_rate * 100) if target_rate > 0 else 0
        return min(round(efficiency, 2), 100)  # Cap at 100%
        
    except Exception as e:
        current_app.logger.error(f"Error calculating efficiency: {str(e)}")
        return 0

def calculate_actual_cycle_time(operator_log):
    """Calculate actual cycle time in minutes for an operator log"""
    try:
        if not operator_log or not operator_log.cycle_start_time:
            return None
            
        # Ensure timezone-aware timestamps
        now = datetime.now(timezone.utc)
        cycle_start = operator_log.cycle_start_time
        if cycle_start.tzinfo is None:
            cycle_start = cycle_start.replace(tzinfo=timezone.utc)
            
        # For completed cycles
        if operator_log.last_cycle_end_time and operator_log.first_cycle_start_time:
            last_end = operator_log.last_cycle_end_time
            first_start = operator_log.first_cycle_start_time
            if last_end.tzinfo is None:
                last_end = last_end.replace(tzinfo=timezone.utc)
            if first_start.tzinfo is None:
                first_start = first_start.replace(tzinfo=timezone.utc)
            total_time = (last_end - first_start).total_seconds()
            total_parts = operator_log.run_completed_quantity or 1
            return round((total_time / 60) / total_parts, 2)  # Average cycle time per part in minutes
            
        # For ongoing cycle
        current_cycle_time = (now - cycle_start).total_seconds() / 60
        return round(current_cycle_time, 2)
            
    except Exception as e:
        current_app.logger.error(f"Error calculating actual cycle time: {str(e)}")
        return None

def calculate_actual_setup_time(operator_log):
    """Calculate actual setup time in minutes for an operator log"""
    try:
        if not operator_log or not operator_log.setup_start_time:
            return None
            
        # Ensure timezone-aware timestamps
        now = datetime.now(timezone.utc)
        setup_start = operator_log.setup_start_time
        if setup_start.tzinfo is None:
            setup_start = setup_start.replace(tzinfo=timezone.utc)
            
        # For completed setup
        if operator_log.setup_end_time:
            setup_end = operator_log.setup_end_time
            if setup_end.tzinfo is None:
                setup_end = setup_end.replace(tzinfo=timezone.utc)
            setup_time = (setup_end - setup_start).total_seconds() / 60
            return round(setup_time, 2)
            
        # For ongoing setup
        current_setup_time = (now - setup_start).total_seconds() / 60
        return round(current_setup_time, 2)
            
    except Exception as e:
        current_app.logger.error(f"Error calculating actual setup time: {str(e)}")
        return None

def get_machine_metrics(db):
    """Get real-time machine metrics"""
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

            # Calculate actual times
            actual_cycle_time = calculate_actual_cycle_time(current_log)
            actual_setup_time = calculate_actual_setup_time(current_log)

            metrics.append({
                'machine_name': machine.name,
                'status': machine.status,
                'operator': active_session.operator_name if active_session else None,
                'current_job': current_log.drawing_rel.drawing_number if current_log and current_log.drawing_rel else None,
                'parts_completed': current_log.run_completed_quantity if current_log else 0,
                'parts_rejected': (current_log.run_rejected_quantity_fpi + current_log.run_rejected_quantity_lpi) if current_log else 0,
                'efficiency': calculate_efficiency(current_log),
                'actual_cycle_time': actual_cycle_time,
                'actual_setup_time': actual_setup_time,
                'std_cycle_time': current_log.standard_cycle_time if current_log else None,
                'std_setup_time': current_log.standard_setup_time if current_log else None,
                'timestamp': datetime.now(timezone.utc).isoformat()
            })

        return metrics
    except Exception as e:
        current_app.logger.error(f"Error getting machine metrics: {str(e)}")
        return []

def generate_dpr_csv(data):
    """Generate CSV data for DPR"""
    try:
        output = StringIO()
        writer = csv.writer(output)
        
        # Write headers
        writer.writerow(['Name', 'SAP ID', 'Planned', 'Completed', 'Rejected', 'Rework', 
                        'Completion Rate (%)', 'Quality Rate (%)', 'Timestamp'])
        
        # Write data rows
        for item in data:
            writer.writerow([
                item['name'],
                item['sap_id'],
                item['planned'],
                item['completed'],
                item['rejected'],
                item['rework'],
                item['completion_rate'],
                item['quality_rate'],
                item['timestamp']
            ])
        
        return output.getvalue()
    except Exception as e:
        current_app.logger.error(f"Error generating DPR CSV: {str(e)}")
        return None

def generate_detailed_dpr_csv(logs):
    """Generate CSV for detailed DPR logs (matches table columns in live_dpr.html)"""
    try:
        output = StringIO()
        writer = csv.writer(output)
        # Write headers (match live_dpr.html table)
        writer.writerow([
            'Date (UTC)', 'Machine', 'Operator', 'Project', 'SAP ID', 'Drawing No',
            'Setup Start (UTC)', 'Setup End (UTC)', 'First Cycle Start (UTC)', 'Last Cycle End (UTC)',
            'Assigned Qty', 'Completed Qty', 'Passed Qty', 'Rejected Qty', 'Rework Qty',
            'Std. Setup (min)', 'Actual Setup (min)', 'Std. Cycle (min)', 'Actual Cycle (min)', 'Total Cycle (min)',
            'FPI Status', 'LPI Status', 'Status', 'Availability (%)', 'Performance (%)', 'Quality (%)', 'OEE (%)'
        ])
        for log in logs:
            try:
                row = [
                    str(log.get('date', '') or ''),
                    str(log.get('mc_do', '') or ''),
                    str(log.get('operator', '') or ''),
                    str(log.get('proj', '') or ''),
                    str(log.get('sap_no', '') or ''),
                    str(log.get('drg_no', '') or ''),
                    str(log.get('setup_start_utc', '') or ''),
                    str(log.get('setup_end_utc', '') or ''),
                    str(log.get('first_cycle_start_utc', '') or ''),
                    str(log.get('last_cycle_end_utc', '') or ''),
                    str(log.get('planned', '') or ''),
                    str(log.get('completed', '') or ''),
                    str(log.get('passed', '') or ''),
                    str(log.get('rejected', '') or ''),
                    str(log.get('rework', '') or ''),
                    str(log.get('std_setup_time', '') or ''),
                    str(log.get('actual_setup_time', '') or ''),
                    str(log.get('std_cycle_time', '') or ''),
                    str(log.get('actual_cycle_time', '') or ''),
                    str(log.get('total_cycle_time', '') or ''),
                    str(log.get('fpi_status', '') or ''),
                    str(log.get('lpi_status', '') or ''),
                    str(log.get('status', '') or ''),
                    str(log.get('availability', '') or ''),
                    str(log.get('performance', '') or ''),
                    str(log.get('quality', '') or ''),
                    str(log.get('oee', '') or '')
                ]
                writer.writerow(row)
            except Exception as row_e:
                current_app.logger.error(f"Error writing DPR CSV row: {row_e} | log: {log}")
                continue
        return output.getvalue()
    except Exception as e:
        current_app.logger.error(f"Error generating detailed DPR CSV: {str(e)}")
        return None