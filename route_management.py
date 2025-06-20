from flask import Blueprint, request, jsonify
from models import db, ProcessRoute, RouteOperation, TransferRequest, LPIRecord, Machine, OperatorLog, MachineDrawingAssignment, MachineDrawing
from datetime import datetime
from flask_login import current_user
import socketio
from sqlalchemy import func

route_bp = Blueprint('route_management', __name__)

@route_bp.route('/api/routes', methods=['GET'])
def get_routes():
    try:
        routes = ProcessRoute.query.all()
        return jsonify({
            'status': 'success',
            'routes': [{
                'id': route.id,
                'sap_id': route.sap_id,
                'total_quantity': route.total_quantity,
                'status': route.status,
                'deadline': route.deadline.strftime('%Y-%m-%d') if route.deadline else None,
                'operations': [{
                    'id': op.id,
                    'operation_type': op.operation_type,
                    'sequence': op.sequence,
                    'assigned_machine': op.assigned_machine,
                    'status': op.status,
                    'completed_quantity': op.completed_quantity
                } for op in route.operations]
            } for route in routes]
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@route_bp.route('/api/routes', methods=['POST'])
def create_route():
    try:
        data = request.json
        new_route = ProcessRoute(
            sap_id=data['sap_id'],
            total_quantity=data['total_quantity'],
            created_by=current_user.username,
            deadline=datetime.strptime(data['deadline'], '%Y-%m-%d') if data.get('deadline') else None,
            status='PENDING'
        )
        db.session.add(new_route)
        db.session.flush()

        # Add operations
        for idx, op in enumerate(data['operations']):
            operation = RouteOperation(
                route_id=new_route.id,
                operation_type=op['type'],
                sequence=idx + 1,
                status='NOT_STARTED'
            )
            db.session.add(operation)

        db.session.commit()
        return jsonify({'status': 'success', 'route_id': new_route.id})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500

@route_bp.route('/api/routes/assign-machine', methods=['POST'])
def assign_machine():
    try:
        data = request.json
        operation = RouteOperation.query.get(data['operation_id'])
        if not operation:
            return jsonify({'status': 'error', 'message': 'Operation not found'}), 404

        operation.assigned_machine = data['machine_id']
        db.session.commit()
        return jsonify({'status': 'success'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500

@route_bp.route('/api/transfer-request', methods=['POST'])
def create_transfer_request():
    try:
        data = request.json
        
        # Get the current route operation
        current_operation = RouteOperation.query.get(data['route_operation_id'])
        if not current_operation:
            return jsonify({
                'status': 'error',
                'message': 'Route operation not found'
            }), 404
            
        # Validate current machine matches assigned machine
        if current_operation.assigned_machine != data['from_machine']:
            return jsonify({
                'status': 'error',
                'message': f'Parts must be processed on {current_operation.assigned_machine}'
            }), 400
        
        # Get next operation in sequence
        next_operation = RouteOperation.query.filter_by(
            route_id=current_operation.route_id,
            sequence=current_operation.sequence + 1
        ).first()
        
        if not next_operation:
            return jsonify({
                'status': 'error',
                'message': 'No next operation found in route'
            }), 400
            
        # Create transfer request with validated next machine
        transfer = TransferRequest(
            route_operation_id=data['route_operation_id'],
            from_machine=data['from_machine'],
            to_machine=next_operation.assigned_machine,  # Use assigned machine from route
            quantity=data['quantity'],
            sap_id=data['sap_id'],
            status='PENDING_LPI'
        )
        
        db.session.add(transfer)
        db.session.commit()
        
        # Emit socket event for real-time updates
        socketio.emit('new_transfer', {
            'transfer_id': transfer.id,
            'sap_id': transfer.sap_id,
            'to_machine': transfer.to_machine
        })
        
        return jsonify({
            'status': 'success',
            'transfer_id': transfer.id,
            'next_machine': next_operation.assigned_machine
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500

@route_bp.route('/api/pending-lpi')
def get_pending_lpi():
    try:
        transfers = TransferRequest.query.filter_by(status='PENDING_LPI').all()
        return jsonify({
            'status': 'success',
            'transfers': [{
                'id': t.id,
                'sap_id': t.sap_id,
                'from_machine': t.from_machine,
                'to_machine': t.to_machine,
                'quantity': t.quantity,
                'status': t.status
            } for t in transfers]
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@route_bp.route('/api/submit-lpi', methods=['POST'])
def submit_lpi():
    try:
        data = request.json
        transfer = TransferRequest.query.get(data['transfer_id'])
        if not transfer:
            return jsonify({'status': 'error', 'message': 'Transfer not found'}), 404
        
        # Create LPI record
        lpi = LPIRecord(
            transfer_id=transfer.id,
            inspector=current_user.username,
            result=data['result'],
            remarks=data.get('remarks', ''),
            dimensions_ok=data['dimensions_ok'],
            surface_finish_ok=data['surface_finish_ok'],
            critical_features_ok=data['critical_features_ok'],
            measurements=data.get('measurements', {})
        )
        db.session.add(lpi)
        
        # Update transfer status
        transfer.status = 'LPI_PASSED' if data['result'] == 'PASS' else 'LPI_FAILED'
        
        db.session.commit()
        
        # Emit socket event
        socketio.emit('lpi_complete', {
            'transfer_id': transfer.id,
            'status': transfer.status,
            'to_machine': transfer.to_machine
        })
        
        return jsonify({'status': 'success'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500

@route_bp.route('/api/reject-transfer', methods=['POST'])
def reject_transfer():
    try:
        data = request.json
        transfer = TransferRequest.query.get(data['transfer_id'])
        if not transfer:
            return jsonify({'status': 'error', 'message': 'Transfer not found'}), 404
        
        transfer.status = 'REJECTED'
        
        # Create rejection record
        lpi = LPIRecord(
            transfer_id=transfer.id,
            inspector=current_user.username,
            result='FAIL',
            remarks=data.get('remarks', 'Transfer rejected'),
            dimensions_ok=False,
            surface_finish_ok=False,
            critical_features_ok=False
        )
        db.session.add(lpi)
        db.session.commit()
        
        # Emit socket event
        socketio.emit('transfer_rejected', {
            'transfer_id': transfer.id,
            'from_machine': transfer.from_machine
        })
        
        return jsonify({'status': 'success'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500

@route_bp.route('/api/incoming-transfers/<machine_id>')
def get_incoming_transfers(machine_id):
    try:
        transfers = TransferRequest.query.filter_by(
            to_machine=machine_id,
            status='LPI_PASSED'
        ).all()
        
        return jsonify({
            'status': 'success',
            'transfers': [{
                'id': t.id,
                'sap_id': t.sap_id,
                'from_machine': t.from_machine,
                'quantity': t.quantity,
                'status': t.status
            } for t in transfers]
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@route_bp.route('/api/accept-transfer', methods=['POST'])
def accept_transfer():
    try:
        data = request.json
        transfer = TransferRequest.query.get(data['transfer_id'])
        if not transfer:
            return jsonify({'status': 'error', 'message': 'Transfer not found'}), 404
            
        # Validate receiving machine
        current_user_machine = data.get('machine_id')  # Get from request
        if current_user_machine != transfer.to_machine:
            return jsonify({
                'status': 'error',
                'message': f'Parts must be received by {transfer.to_machine}'
            }), 400
        
        # Update transfer status
        transfer.status = 'COMPLETED'
        transfer.completed_at = datetime.utcnow()
        
        # Update route operation progress
        operation = RouteOperation.query.get(transfer.route_operation_id)
        if operation:
            operation.completed_quantity += transfer.quantity
        
        db.session.commit()
        
        # Emit socket event
        socketio.emit('transfer_completed', {
            'transfer_id': transfer.id,
            'sap_id': transfer.sap_id,
            'quantity': transfer.quantity
        })
        
        return jsonify({'status': 'success'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500

@route_bp.route('/api/available-machines/<operation_type>')
def get_available_machines(operation_type):
    """Get available machines for an operation type"""
    try:
        machines = RouteOperation.get_available_machines(operation_type)
        return jsonify({
            'status': 'success',
            'machines': machines
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@route_bp.route('/api/machine-statuses')
def get_machine_statuses():
    """Get current status of all machines"""
    try:
        # Get all machines from database
        all_machines = Machine.query.all()
        
        # Group machines by type
        tapping_machines = []
        welding_machines = []
        milling_machines = []
        lathe_machines = []
        vmc_machines = []
        cnc_machines = []
        bandsaw_machines = []
        
        for machine in all_machines:
            # Get current job for this machine
            current_job = None
            try:
                # Look for active operator log for this machine
                active_log = OperatorLog.query.filter_by(
                    machine_id=machine.name,
                    current_status='IN_PROGRESS'
                ).first()
                
                if active_log:
                    current_job = {
                        'sap_id': active_log.sap_id or 'N/A',
                        'completed': active_log.run_completed_quantity or 0,
                        'total': active_log.run_planned_quantity or 0
                    }
            except Exception as e:
                print(f"Error getting current job for {machine.name}: {e}")
            
            # --- PATCH: Exclude unwanted machines ---
            if machine.name in ['MILLING MACHINE 2', 'MILLING MACHINE 3', 'MILLING MACHINE 4', 'MANUAL LATHE 2']:
                continue  # Skip these
            if 'LATHE' in machine.name.upper() and machine.name != 'MANUAL LATHE 1' and not machine.name.startswith('CNC LATHE'):
                continue  # Remove all other manual lathes
            # --- END PATCH ---

            machine_data = {
                'name': machine.name,
                'status': machine.status.upper() if machine.status else 'IDLE',
                'currentJob': current_job
            }
            
            # Categorize machines based on name patterns
            machine_name_upper = machine.name.upper()
            if 'TAP' in machine_name_upper or 'TAPPING' in machine_name_upper:
                tapping_machines.append(machine_data)
            elif 'WELD' in machine_name_upper or 'MIG' in machine_name_upper or 'TIG' in machine_name_upper:
                welding_machines.append(machine_data)
            elif 'MILL' in machine_name_upper:
                if machine.name == 'MILLING MACHINE 1':
                    milling_machines.append(machine_data)
            elif 'LATHE' in machine_name_upper:
                if machine.name == 'MANUAL LATHE 1':
                    lathe_machines.append(machine_data)
            elif machine.machine_type == 'VMC':
                vmc_machines.append(machine_data)
            elif machine.machine_type == 'CNC':
                cnc_machines.append(machine_data)
            elif machine.machine_type == 'BANDSAW':
                bandsaw_machines.append(machine_data)
            else:
                # Default to VMC if no specific category
                vmc_machines.append(machine_data)
        
        # If no specific machines found, create default ones
        if not tapping_machines:
            tapping_machines = [
                {'name': 'TAPPING MACHINE 1', 'status': 'IDLE', 'currentJob': None},
                {'name': 'TAPPING MACHINE 2', 'status': 'IDLE', 'currentJob': None},
                {'name': 'TAPPING MACHINE 3', 'status': 'IDLE', 'currentJob': None}
            ]
        
        if not welding_machines:
            welding_machines = [
                {'name': 'MIG WELDING', 'status': 'IDLE', 'currentJob': None},
                {'name': 'TIG WELDING', 'status': 'IDLE', 'currentJob': None},
                {'name': 'LASER WELDING', 'status': 'IDLE', 'currentJob': None},
                {'name': 'ARC WELDING', 'status': 'IDLE', 'currentJob': None}
            ]
        
        if not milling_machines:
            milling_machines = [
                {'name': 'MILLING MACHINE', 'status': 'IDLE', 'currentJob': None}
            ]
        
        if not lathe_machines:
            lathe_machines = [
                {'name': 'MANUAL LATHE', 'status': 'IDLE', 'currentJob': None}
            ]
        
        return jsonify({
            'status': 'success',
            'tappingMachines': tapping_machines,
            'weldingMachines': welding_machines,
            'millingMachines': milling_machines,
            'latheMachines': lathe_machines,
            'vmcMachines': vmc_machines,
            'cncMachines': cnc_machines,
            'bandsawMachines': bandsaw_machines,
            'millingMachine': milling_machines[0] if milling_machines else {'name': 'MILLING', 'status': 'IDLE', 'currentJob': None},
            'manualLathe': lathe_machines[0] if lathe_machines else {'name': 'MANUAL LATHE', 'status': 'IDLE', 'currentJob': None}
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@route_bp.route('/api/validate-machine', methods=['POST'])
def validate_machine():
    """Validate if a machine can be assigned to an operation"""
    try:
        data = request.json
        operation_type = data.get('operation_type')
        machine_name = data.get('machine_name')
        
        if not operation_type or not machine_name:
            return jsonify({
                'status': 'error',
                'message': 'Operation type and machine name are required'
            }), 400
            
        is_valid = RouteOperation.validate_machine_assignment(
            operation_type,
            machine_name
        )
        
        return jsonify({
            'status': 'success',
            'valid': is_valid
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@route_bp.route('/api/suggest-machines/<operation_id>')
def suggest_machines(operation_id):
    try:
        # Get the route operation
        operation = RouteOperation.query.get(operation_id)
        if not operation:
            return jsonify({'status': 'error', 'message': 'Operation not found'}), 404

        # Get all available machines for this operation type
        available_machines = RouteOperation.get_available_machines(operation.operation_type)
        
        # Get machine statuses to enhance suggestions
        machine_statuses = {
            m.name: {
                'status': m.status,
                'current_load': len(m.drawing_assignments)
            }
            for m in Machine.query.filter(Machine.name.in_(available_machines)).all()
        }
        
        # Sort machines by status and load
        sorted_machines = sorted(
            available_machines,
            key=lambda m: (
                0 if machine_statuses.get(m, {}).get('status') == 'IDLE' else 1,
                machine_statuses.get(m, {}).get('current_load', 0)
            )
        )

        return jsonify({
            'status': 'success',
            'suggestions': [{
                'machine_name': machine,
                'status': machine_statuses.get(machine, {}).get('status', 'UNKNOWN'),
                'current_load': machine_statuses.get(machine, {}).get('current_load', 0)
            } for machine in sorted_machines]
        })

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@route_bp.route('/api/pending_routes')
def get_pending_routes():
    """Get all pending routes for the machine shop"""
    try:
        routes = ProcessRoute.query.filter_by(status='PENDING').all()
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
        
        return jsonify({
            'status': 'success',
            'routes': routes_data
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@route_bp.route('/api/assign_machine', methods=['POST'])
def assign_machine_to_operation():
    """Assign a machine to a route operation"""
    try:
        data = request.json
        operation_id = data.get('operation_id')
        machine_name = data.get('machine_name')
        
        if not operation_id or not machine_name:
            return jsonify({
                'status': 'error',
                'message': 'Operation ID and machine name are required'
            }), 400
        
        operation = RouteOperation.query.get(operation_id)
        if not operation:
            return jsonify({
                'status': 'error',
                'message': 'Operation not found'
            }), 404
        
        # Validate machine assignment
        if not RouteOperation.validate_machine_assignment(operation.operation_type, machine_name):
            return jsonify({
                'status': 'error',
                'message': f'Machine {machine_name} cannot be assigned to operation type {operation.operation_type}'
            }), 400
        
        operation.assigned_machine = machine_name
        db.session.commit()
        
        return jsonify({'status': 'success'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500

@route_bp.route('/api/clear_machine_assignment', methods=['POST'])
def clear_machine_assignment():
    """Clear machine assignment from a route operation"""
    try:
        data = request.json
        operation_id = data.get('operation_id')
        
        if not operation_id:
            return jsonify({
                'status': 'error',
                'message': 'Operation ID is required'
            }), 400
        
        operation = RouteOperation.query.get(operation_id)
        if not operation:
            return jsonify({
                'status': 'error',
                'message': 'Operation not found'
            }), 404
        
        operation.assigned_machine = None
        db.session.commit()
        
        return jsonify({'status': 'success'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500

@route_bp.route('/api/start_route', methods=['POST'])
def start_route():
    """Start a route by changing its status to IN_PROGRESS"""
    try:
        data = request.json
        route_id = data.get('route_id')
        
        if not route_id:
            return jsonify({
                'status': 'error',
                'message': 'Route ID is required'
            }), 400
        
        route = ProcessRoute.query.get(route_id)
        if not route:
            return jsonify({
                'status': 'error',
                'message': 'Route not found'
            }), 404
        
        # Check if all operations have machines assigned
        operations = RouteOperation.query.filter_by(route_id=route_id).all()
        if not all(op.assigned_machine for op in operations):
            return jsonify({
                'status': 'error',
                'message': 'All operations must have machines assigned before starting the route'
            }), 400
        
        route.status = 'IN_PROGRESS'
        db.session.commit()
        
        return jsonify({'status': 'success'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500

@route_bp.route('/api/sap_suggestions')
def sap_suggestions():
    q = request.args.get('q', '').strip()
    if not q or len(q) < 2:
        return jsonify({'suggestions': []})
    # Suggest SAP IDs from ProcessRoute
    sap_ids = db.session.query(ProcessRoute.sap_id).filter(ProcessRoute.sap_id.ilike(f'%{q}%')).distinct().limit(10).all()
    return jsonify({'suggestions': [row[0] for row in sap_ids]})

@route_bp.route('/api/route_info/<sap_id>')
def route_info(sap_id):
    route = ProcessRoute.query.filter_by(sap_id=sap_id).first()
    if not route:
        return jsonify({'error': 'Route not found'}), 404
    # Get all operations in sequence
    operations = RouteOperation.query.filter_by(route_id=route.id).order_by(RouteOperation.sequence).all()
    # Get total and completed quantity
    total_quantity = route.total_quantity
    completed_quantity = sum(op.completed_quantity or 0 for op in operations)
    # Remaining = total - sum of all assigned quantities (across all assignments for this SAP ID)
    assigned_quantity = sum(op.completed_quantity or 0 for op in operations)
    remaining_quantity = max(total_quantity - assigned_quantity, 0)
    # In progress warning if any op.completed_quantity > 0
    in_progress = any((op.completed_quantity or 0) > 0 for op in operations)
    # Assignment/transfer log (simple for now)
    log = [f"Op {op.sequence} ({op.operation_type}): {op.completed_quantity or 0} completed, assigned to {op.assigned_machine or '-'}" for op in operations]
    # Build route_str as a readable string from operation sequence
    route_str = ' → '.join([op.operation_type for op in operations])
    return jsonify({
        'route': {
            'sap_id': route.sap_id,
            'completed_quantity': completed_quantity,
            'route_str': route_str
        },
        'total_quantity': total_quantity,
        'remaining_quantity': remaining_quantity,
        'operations': [
            {
                'id': op.id,
                'operation_type': op.operation_type,
                'assigned_machine': op.assigned_machine,
                'status': op.status,
                'sequence': op.sequence
            } for op in operations
        ],
        'in_progress': in_progress,
        'assignment_log': log
    })

@route_bp.route('/api/update_route_sequence', methods=['POST'])
def update_route_sequence():
    data = request.json
    sap_id = data.get('sap_id')
    operations = data.get('operations', [])
    route = ProcessRoute.query.filter_by(sap_id=sap_id).first()
    if not route:
        return jsonify({'error': 'Route not found'}), 404
    # Check if any operation is in progress
    in_progress = any((op.completed_quantity or 0) > 0 for op in RouteOperation.query.filter_by(route_id=route.id))
    # Update sequence (delete old, add new in order)
    RouteOperation.query.filter_by(route_id=route.id).delete()
    for idx, op in enumerate(operations):
        new_op = RouteOperation(
            route_id=route.id,
            operation_type=op.get('operation_type'),
            assigned_machine=op.get('assigned_machine'),
            sequence=idx+1,
            status=op.get('status', 'NOT_STARTED'),
            completed_quantity=0
        )
        db.session.add(new_op)
    db.session.commit()
    return jsonify({'status': 'success', 'in_progress': in_progress})

@route_bp.route('/api/assign_route', methods=['POST'])
def assign_route():
    data = request.json
    sap_id = data.get('sap_id')
    quantity = int(data.get('quantity', 0))
    operations = data.get('operations', [])
    route = ProcessRoute.query.filter_by(sap_id=sap_id).first()
    if not route or not operations or quantity < 1:
        return jsonify({'error': 'Invalid data'}), 400
    # Assign machines and quantity for each operation
    from models import MachineDrawingAssignment, MachineDrawing, Machine, User
    for idx, op_data in enumerate(operations):
        op = RouteOperation.query.filter_by(route_id=route.id, sequence=idx+1).first()
        if not op:
            continue
        op.assigned_machine = op_data.get('assigned_machine')
        op.status = 'assigned'
        # Only assign quantity to the first operation; others will be updated as the process flows
        if idx == 0:
            op.completed_quantity = (op.completed_quantity or 0) + quantity
        # --- PATCH: Create MachineDrawingAssignment if not exists ---
        drawing = MachineDrawing.query.filter_by(sap_id=sap_id).first()
        machine = Machine.query.filter_by(name=op.assigned_machine).first()
        if drawing and machine:
            existing = MachineDrawingAssignment.query.filter_by(drawing_id=drawing.id, machine_id=machine.id).first()
            if not existing:
                assignment = MachineDrawingAssignment(
                    drawing_id=drawing.id,
                    machine_id=machine.id,
                    assigned_quantity=quantity if idx == 0 else 0,
                    tool_number=op_data.get('tool_number', ''),
                    status='assigned',
                    assigned_by=getattr(current_user, 'id', 1),
                    assigned_at=datetime.now()
                )
                db.session.add(assignment)
    db.session.commit()
    return jsonify({'status': 'success'}) 