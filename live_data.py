from datetime import datetime, timezone, timedelta
from flask import current_app
import csv
from io import StringIO
from utils import validate_production_data

def get_live_production_data(db):
    """Get current production data for DPR"""
    try:
        from models import EndProduct, OperatorLog
        
        # Get all end products
        end_products = EndProduct.query.all()
        production_data = []
        
        for product in end_products:
            # Validate SAP ID and quantity
            if not validate_production_data(product.sap_id, product.quantity):
                current_app.logger.warning(f"Invalid production data for product {product.name}: SAP ID={product.sap_id}, Quantity={product.quantity}")
                continue
                
            # Get completed, rejected, and rework quantities
            completed = db.session.query(db.func.coalesce(db.func.sum(OperatorLog.run_completed_quantity), 0))\
                .filter(
                    OperatorLog.end_product_sap_id == product.sap_id,
                    OperatorLog.run_completed_quantity > 0
                ).scalar() or 0
                
            rejected = db.session.query(db.func.coalesce(db.func.sum(
                OperatorLog.run_rejected_quantity_fpi + OperatorLog.run_rejected_quantity_lpi), 0))\
                .filter(
                    OperatorLog.end_product_sap_id == product.sap_id,
                    OperatorLog.run_rejected_quantity_fpi >= 0,
                    OperatorLog.run_rejected_quantity_lpi >= 0
                ).scalar() or 0
                
            rework = db.session.query(db.func.coalesce(db.func.sum(
                OperatorLog.run_rework_quantity_fpi + OperatorLog.run_rework_quantity_lpi), 0))\
                .filter(
                    OperatorLog.end_product_sap_id == product.sap_id,
                    OperatorLog.run_rework_quantity_fpi >= 0,
                    OperatorLog.run_rework_quantity_lpi >= 0
                ).scalar() or 0
            
            production_data.append({
                'name': str(product.name),
                'sap_id': str(product.sap_id),
                'planned': int(product.quantity),
                'completed': int(completed),
                'rejected': int(rejected),
                'rework': int(rework),
                'completion_rate': round((completed / product.quantity * 100 if product.quantity > 0 else 0), 2),
                'quality_rate': round(((completed - rejected - rework) / completed * 100 if completed > 0 else 0), 2),
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

            metrics.append({
                'machine_name': machine.name,
                'status': machine.status,
                'operator': active_session.operator_name if active_session else None,
                'current_job': current_log.drawing_rel.drawing_number if current_log and current_log.drawing_rel else None,
                'parts_completed': current_log.run_completed_quantity if current_log else 0,
                'parts_rejected': (current_log.run_rejected_quantity_fpi + current_log.run_rejected_quantity_lpi) if current_log else 0,
                'efficiency': calculate_efficiency(current_log),
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