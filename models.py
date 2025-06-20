from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone
from sqlalchemy import func, case
from sqlalchemy.orm import relationship
from sqlalchemy import event

# Initialize SQLAlchemy without binding to app
db = SQLAlchemy()

class Machine(db.Model):
    __tablename__ = 'machine'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    status = db.Column(db.String(50), nullable=False)
    machine_type = db.Column(db.String(20), nullable=False, default='VMC')  # VMC or CNC
    production_records = relationship("ProductionRecord", back_populates="machine")
    drawing_assignments = relationship("MachineDrawingAssignment", back_populates="machine_rel", cascade="all, delete-orphan")
    breakdowns = relationship("MachineBreakdownLog", back_populates="machine_rel", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'status': self.status,
            'machine_type': self.machine_type
        }

class Project(db.Model):
    __tablename__ = 'project'
    id = db.Column(db.Integer, primary_key=True)
    project_code = db.Column(db.String(50), unique=True, nullable=False)
    project_name = db.Column(db.String(100), nullable=False)
    is_deleted = db.Column(db.Boolean, default=False)
    deleted_at = db.Column(db.DateTime)
    end_products = relationship("EndProduct", back_populates="project_rel")

class MachineDrawing(db.Model):
    __tablename__ = 'machine_drawing'
    id = db.Column(db.Integer, primary_key=True)
    drawing_number = db.Column(db.String(100), unique=True, nullable=False)
    sap_id = db.Column(db.String(50), db.ForeignKey('end_product.sap_id'), nullable=False)
    
    end_product_rel = relationship("EndProduct", back_populates="machine_drawings")
    operator_logs = relationship("OperatorLog", foreign_keys='OperatorLog.drawing_id', back_populates="drawing_rel", cascade="all, delete-orphan")
    rework_items = relationship("ReworkQueue", foreign_keys='ReworkQueue.drawing_id', back_populates="drawing_rel")
    scrapped_items = relationship("ScrapLog", foreign_keys='ScrapLog.drawing_id', back_populates="drawing_rel")
    assignments = relationship("MachineDrawingAssignment", back_populates="drawing_rel", cascade="all, delete-orphan")

class EndProduct(db.Model):
    __tablename__ = 'end_product'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    sap_id = db.Column(db.String(50), nullable=False, unique=True)
    quantity = db.Column(db.Integer, nullable=False)
    completion_date = db.Column(db.Date, nullable=False)
    setup_time_std = db.Column(db.Float, nullable=False)
    cycle_time_std = db.Column(db.Float, nullable=False)
    is_first_piece_fpi_required = db.Column(db.Boolean, nullable=False, default=True)
    is_last_piece_lpi_required = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.now(timezone.utc))
    
    project_rel = relationship("Project", back_populates="end_products")
    machine_drawings = relationship("MachineDrawing", back_populates="end_product_rel", cascade="all, delete-orphan")
    production_records = relationship("ProductionRecord", back_populates="end_product")
    operator_logs_for_sap = relationship("OperatorLog", foreign_keys='OperatorLog.end_product_sap_id', back_populates="end_product_sap_id_rel")

class ProductionRecord(db.Model):
    __tablename__ = 'production_record'
    id = db.Column(db.Integer, primary_key=True)
    machine_id = db.Column(db.Integer, db.ForeignKey('machine.id'), nullable=False)
    end_product_id = db.Column(db.Integer, db.ForeignKey('end_product.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.now(timezone.utc))
    shift = db.Column(db.String(20))
    
    machine = relationship("Machine", back_populates="production_records")
    end_product = relationship("EndProduct", back_populates="production_records")

class OperatorLog(db.Model):
    __tablename__ = 'operator_log'
    id = db.Column(db.Integer, primary_key=True)
    drawing_number = db.Column(db.String(100))
    drawing_id = db.Column(db.Integer, db.ForeignKey('machine_drawing.id'), nullable=True)
    assignment_id = db.Column(db.Integer, db.ForeignKey('machine_drawing_assignment.id'), nullable=True)
    
    # Batch tracking fields
    batch_id = db.Column(db.Integer, db.ForeignKey('batch.id'))  # Now integer FK
    
    # Time tracking fields
    setup_start_time = db.Column(db.DateTime(timezone=True))  # For accurate setup time calculation
    setup_end_time = db.Column(db.DateTime(timezone=True))
    first_cycle_start_time = db.Column(db.DateTime(timezone=True))
    last_cycle_end_time = db.Column(db.DateTime(timezone=True))
    cycle_start_time = db.Column(db.DateTime(timezone=True))  # For current cycle
    
    # Status and timing fields
    current_status = db.Column(db.String(50))
    setup_time = db.Column(db.Float)  # Actual setup time in minutes
    cycle_time = db.Column(db.Float)  # Actual cycle time in minutes
    standard_setup_time = db.Column(db.Float, default=0)  # Standard setup time in minutes
    standard_cycle_time = db.Column(db.Float, default=0)  # Standard cycle time in minutes
    
    # General fields
    abort_reason = db.Column(db.Text)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), default=datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime(timezone=True), onupdate=datetime.now(timezone.utc))
    batch_number = db.Column(db.String(50))
    
    # Production quantities
    run_planned_quantity = db.Column(db.Integer, default=0)
    run_completed_quantity = db.Column(db.Integer, default=0)
    run_rejected_quantity_fpi = db.Column(db.Integer, default=0)
    run_rejected_quantity_lpi = db.Column(db.Integer, default=0)
    run_rework_quantity_fpi = db.Column(db.Integer, default=0)
    run_rework_quantity_lpi = db.Column(db.Integer, default=0)
    
    # Operator and machine info
    quality_status = db.Column(db.String(20))
    operator_id = db.Column(db.String(50))
    machine_id = db.Column(db.String(50))
    shift = db.Column(db.String(20))
    
    # Quality check fields
    fpi_status = db.Column(db.String(20), default='Pending')
    fpi_timestamp = db.Column(db.DateTime(timezone=True))
    fpi_inspector = db.Column(db.String(50))
    lpi_status = db.Column(db.String(20))
    lpi_timestamp = db.Column(db.DateTime(timezone=True))
    lpi_inspector = db.Column(db.String(50))
    production_hold_fpi = db.Column(db.Boolean, default=True)
    
    # Drawing and product info
    drawing_revision = db.Column(db.String(20))
    sap_id = db.Column(db.String(100))
    end_product_sap_id = db.Column(db.String(50), db.ForeignKey('end_product.sap_id'), nullable=True)
    operator_session_id = db.Column(db.Integer, db.ForeignKey('operator_session.id'))
    
    # Performance metrics
    downtime_minutes = db.Column(db.Float, default=0)  # Total downtime in minutes
    downtime_category = db.Column(db.String(20))  # planned, unplanned, maintenance
    oee = db.Column(db.Float, default=0)  # Overall Equipment Effectiveness
    availability = db.Column(db.Float, default=0)  # Machine availability percentage
    performance = db.Column(db.Float, default=0)  # Machine performance percentage
    quality_rate = db.Column(db.Float, default=0)  # Quality rate percentage
    total_runtime = db.Column(db.Float, default=0)  # Total runtime in minutes
    actual_runtime = db.Column(db.Float, default=0)  # Actual runtime excluding downtimes
    ideal_cycle_time = db.Column(db.Float)  # Ideal cycle time for the part
    actual_cycle_time = db.Column(db.Float)  # Actual cycle time achieved
    
    # Production metrics
    total_parts_target = db.Column(db.Integer, default=0)  # Target number of parts
    total_parts_produced = db.Column(db.Integer, default=0)  # Total parts produced
    good_parts = db.Column(db.Integer, default=0)  # Number of good parts
    scrap_parts = db.Column(db.Integer, default=0)  # Number of scrapped parts
    rework_parts = db.Column(db.Integer, default=0)  # Number of parts for rework
    mtbf = db.Column(db.Float, default=0)  # Mean Time Between Failures
    mttr = db.Column(db.Float, default=0)  # Mean Time To Repair
    
    # Relationships
    end_product_sap_id_rel = relationship("EndProduct", back_populates="operator_logs_for_sap")
    drawing_rel = relationship("MachineDrawing", back_populates="operator_logs")
    assignment = relationship("MachineDrawingAssignment", backref="operator_logs")
    quality_checks = relationship('QualityCheck', backref='operator_log', lazy=True)
    rework_items_sourced = relationship("ReworkQueue", foreign_keys='ReworkQueue.source_operator_log_id', back_populates="source_operator_log_rel")
    rework_attempt_for_queue = relationship("ReworkQueue", foreign_keys='ReworkQueue.assigned_operator_log_id', back_populates="assigned_operator_log_rel", uselist=False)
    scrapped_items = relationship("ScrapLog", foreign_keys='ScrapLog.operator_log_id', back_populates="operator_log_rel")
    
    def __repr__(self):
        return f'<OperatorLog {self.id}>'

class ReworkQueue(db.Model):
    __tablename__ = 'rework_queue'
    id = db.Column(db.Integer, primary_key=True)
    drawing_id = db.Column(db.Integer, db.ForeignKey('machine_drawing.id'), nullable=False)
    source_operator_log_id = db.Column(db.Integer, db.ForeignKey('operator_log.id'), nullable=False)
    assigned_operator_log_id = db.Column(db.Integer, db.ForeignKey('operator_log.id'), nullable=True)
    originating_quality_check_id = db.Column(db.Integer, db.ForeignKey('quality_check.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, onupdate=datetime.now(timezone.utc))
    
    drawing_rel = relationship("MachineDrawing", back_populates="rework_items")
    source_operator_log_rel = relationship("OperatorLog", foreign_keys=[source_operator_log_id], back_populates="rework_items_sourced")
    assigned_operator_log_rel = relationship("OperatorLog", foreign_keys=[assigned_operator_log_id], back_populates="rework_attempt_for_queue")
    originating_quality_check_rel = relationship("QualityCheck", back_populates="rework_items_generated")

class QualityCheck(db.Model):
    __tablename__ = 'quality_check'
    id = db.Column(db.Integer, primary_key=True)
    operator_log_id = db.Column(db.Integer, db.ForeignKey('operator_log.id'), nullable=False)
    inspector_name = db.Column(db.String(100), nullable=False)
    check_type = db.Column(db.String(20), nullable=False)
    result = db.Column(db.String(10), nullable=False)
    lpi_quantity_inspected = db.Column(db.Integer, nullable=True)
    lpi_quantity_rejected = db.Column(db.Integer, nullable=True)
    lpi_quantity_to_rework = db.Column(db.Integer, nullable=True)
    rejection_reason = db.Column(db.Text, nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.now(timezone.utc))
    
    # Batch tracking fields
    batch_id = db.Column(db.Integer, db.ForeignKey('batch.id'))  # Now integer FK
    
    operator_log_rel = relationship("OperatorLog", back_populates="quality_checks", overlaps="operator_log")
    rework_items_generated = relationship("ReworkQueue", foreign_keys='ReworkQueue.originating_quality_check_id', back_populates="originating_quality_check_rel")
    scrapped_items_generated = relationship("ScrapLog", foreign_keys='ScrapLog.originating_quality_check_id', back_populates="originating_quality_check_rel")

class ScrapLog(db.Model):
    __tablename__ = 'scrap_log'
    id = db.Column(db.Integer, primary_key=True)
    drawing_id = db.Column(db.Integer, db.ForeignKey('machine_drawing.id'), nullable=False)
    operator_log_id = db.Column(db.Integer, db.ForeignKey('operator_log.id'), nullable=False)
    originating_quality_check_id = db.Column(db.Integer, db.ForeignKey('quality_check.id'), nullable=False)
    quantity_scrapped = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.Text, nullable=False)
    scrapped_at = db.Column(db.DateTime, default=datetime.now(timezone.utc))
    scrapped_by = db.Column(db.String(100), nullable=True)
    
    drawing_rel = relationship("MachineDrawing", back_populates="scrapped_items")
    operator_log_rel = relationship("OperatorLog", back_populates="scrapped_items")
    originating_quality_check_rel = relationship("QualityCheck", back_populates="scrapped_items_generated")

class User(db.Model):
    __tablename__ = 'user'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    is_active = db.Column(db.Boolean, default=True)

class SystemLog(db.Model):
    __tablename__ = 'system_log'
    id = db.Column(db.Integer, primary_key=True)
    level = db.Column(db.String(20), nullable=False)
    source = db.Column(db.String(50), nullable=False)
    message = db.Column(db.Text, nullable=False)
    stack_trace = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.now(timezone.utc))
    resolved = db.Column(db.Boolean, default=False)
    resolved_by = db.Column(db.String(80))
    resolved_at = db.Column(db.DateTime)

class OperatorSession(db.Model):
    __tablename__ = 'operator_session'
    id = db.Column(db.Integer, primary_key=True)
    operator_name = db.Column(db.String(100), nullable=False)
    machine_id = db.Column(db.Integer, db.ForeignKey('machine.id'), nullable=False)
    login_time = db.Column(db.DateTime(timezone=True), default=datetime.now(timezone.utc))
    logout_time = db.Column(db.DateTime(timezone=True))
    is_active = db.Column(db.Boolean, default=True)
    shift = db.Column(db.String(20))
    start_time = db.Column(db.DateTime(timezone=True), default=datetime.now(timezone.utc))  # For session duration tracking
    end_time = db.Column(db.DateTime(timezone=True))  # For session duration tracking
    total_parts = db.Column(db.Integer, default=0)  # Total parts produced in session
    total_downtime = db.Column(db.Float, default=0)  # Total downtime in minutes
    total_setup_time = db.Column(db.Float, default=0)  # Total setup time in minutes
    total_cycle_time = db.Column(db.Float, default=0)  # Total cycle time in minutes
    total_rejected_parts = db.Column(db.Integer, default=0)  # Total rejected parts
    total_rework_parts = db.Column(db.Integer, default=0)  # Total parts sent for rework
    session_oee = db.Column(db.Float, default=0)  # Session OEE percentage
    
    machine_rel = relationship("Machine")
    operator_logs = relationship("OperatorLog", backref="operator_session")

class MachineBreakdownLog(db.Model):
    __tablename__ = 'machine_breakdown_log'
    id = db.Column(db.Integer, primary_key=True)
    machine_id = db.Column(db.Integer, db.ForeignKey('machine.id'), nullable=False)
    breakdown_start_time = db.Column(db.DateTime(timezone=True), nullable=False, default=datetime.now(timezone.utc))
    breakdown_end_time = db.Column(db.DateTime(timezone=True))
    reason = db.Column(db.Text)  # This is the breakdown reason
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), default=datetime.now(timezone.utc))
    breakdown_type = db.Column(db.String(20), nullable=True)  # planned, unplanned, maintenance, setup
    is_active = db.Column(db.Boolean, default=True)  # Track if breakdown is active
    
    machine_rel = relationship("Machine", back_populates="breakdowns")

class MachineDrawingAssignment(db.Model):
    __tablename__ = 'machine_drawing_assignment'
    id = db.Column(db.Integer, primary_key=True)
    drawing_id = db.Column(db.Integer, db.ForeignKey('machine_drawing.id'), nullable=False)
    machine_id = db.Column(db.Integer, db.ForeignKey('machine.id'), nullable=False)
    assigned_quantity = db.Column(db.Integer, nullable=False)
    completed_quantity = db.Column(db.Integer, default=0)
    shipped_quantity = db.Column(db.Integer, default=0)  # Track shipped parts
    tool_number = db.Column(db.String(50))
    status = db.Column(db.String(20), default='assigned')  # assigned, running, completed, transferred
    assigned_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    assigned_at = db.Column(db.DateTime, default=datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, onupdate=datetime.now(timezone.utc))

    # Relationships
    drawing_rel = relationship("MachineDrawing", back_populates="assignments")
    machine_rel = relationship("Machine", back_populates="drawing_assignments")
    assigned_by_rel = relationship("User", foreign_keys=[assigned_by])
    transfer_history = relationship("TransferHistory", back_populates="assignment_rel")

    def __repr__(self):
        return f"<MachineDrawingAssignment {self.drawing_rel.drawing_number} on {self.machine_rel.name}>"

class TransferHistory(db.Model):
    __tablename__ = 'transfer_history'
    id = db.Column(db.Integer, primary_key=True)
    assignment_id = db.Column(db.Integer, db.ForeignKey('machine_drawing_assignment.id'), nullable=False)
    from_machine_id = db.Column(db.Integer, db.ForeignKey('machine.id'), nullable=False)
    to_machine_id = db.Column(db.Integer, db.ForeignKey('machine.id'), nullable=False)
    quantity_transferred = db.Column(db.Integer, nullable=False)
    transferred_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    transferred_at = db.Column(db.DateTime, default=datetime.now(timezone.utc))
    reason = db.Column(db.String(200))

    # Relationships
    assignment_rel = relationship("MachineDrawingAssignment", back_populates="transfer_history")
    from_machine_rel = relationship("Machine", foreign_keys=[from_machine_id])
    to_machine_rel = relationship("Machine", foreign_keys=[to_machine_id])
    transferred_by_rel = relationship("User", foreign_keys=[transferred_by])

    def __repr__(self):
        return f"<TransferHistory {self.assignment_rel.drawing_rel.drawing_number} from {self.from_machine_rel.name} to {self.to_machine_rel.name}>"

class ProcessRoute(db.Model):
    __tablename__ = 'process_routes'
    
    id = db.Column(db.Integer, primary_key=True)
    sap_id = db.Column(db.String(50), nullable=False)
    total_quantity = db.Column(db.Integer, nullable=False)
    created_by = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(50), default='PENDING')
    deadline = db.Column(db.DateTime)
    operations = db.relationship('RouteOperation', backref='route', lazy=True)

class RouteOperation(db.Model):
    __tablename__ = 'route_operations'
    
    id = db.Column(db.Integer, primary_key=True)
    route_id = db.Column(db.Integer, db.ForeignKey('process_routes.id'))
    operation_type = db.Column(db.String(50))  # CNC, VMC, TAPPING_GROUP, etc.
    sequence = db.Column(db.Integer)
    assigned_machine = db.Column(db.String(50))  # Specific machine (e.g., 'TAP1', 'MIG1')
    status = db.Column(db.String(50), default='NOT_STARTED')
    completed_quantity = db.Column(db.Integer, default=0)
    
    # Add validation methods
    @staticmethod
    def validate_machine_assignment(operation_type, machine_name):
        """Validate if machine matches operation type"""
        machine_groups = {
            'CNC': ['CNC'],
            'VMC': ['VMC', 'VC', 'Leadwell', 'AMS VMC', 'HAAS', 'IRUS'],
            'BANDSAW': ['BANDSAW', 'BNDSAW'],
            'TAPPING_GROUP': {
                'prefixes': ['TAP'],
                'machines': ['TAPPING MACHINE 1', 'TAPPING MACHINE 2', 'TAPPING MACHINE 3', 'TAPPING MACHINE 4']
            },
            'MILLING': {
                'prefixes': ['MILL'],
                'machines': ['MILLING MACHINE 1', 'MILLING MACHINE 3', 'MILLING MACHINE 4']
            },
            'MANUAL_LATHE': {
                'prefixes': ['MANUAL LATHE'],
                'machines': ['MANUAL LATHE 1']
            },
            'LATHE': {
                'prefixes': ['LATHE'],
                'machines': ['MANUAL LATHE 1', 'CNC LATHE 1', 'CNC LATHE 2']
            },
            'WELDING_GROUP': {
                'prefixes': ['WELD'],
                'machines': ['MIG WELDING', 'TIG WELDING', 'LASER WELDING', 'ARC WELDING']
            }
        }
        
        group_info = machine_groups.get(operation_type.upper())
        if not group_info:
            return False
            
        # If it's a simple prefix list
        if isinstance(group_info, list):
            return any(machine_name.upper().startswith(prefix) for prefix in group_info)
            
        # If it's a group with specific machines
        if isinstance(group_info, dict):
            # Check both prefixes and exact machine names
            machine_upper = machine_name.upper()
            prefix_match = any(machine_upper.startswith(prefix) for prefix in group_info['prefixes'])
            exact_match = machine_upper in [m.upper() for m in group_info['machines']]
            return prefix_match or exact_match
            
        return False
    
    def is_valid_next_machine(self, machine_name):
        """Check if a machine is valid for this operation"""
        return self.validate_machine_assignment(self.operation_type, machine_name)
    
    @staticmethod
    def get_available_machines(operation_type):
        """Get list of available machines for an operation type"""
        machine_options = {
            'CNC': ['CNC1', 'CNC2', 'CNC3', 'CNC4', 'CNC5', 'CNC6', 'CNC7', 'CNC8', 'CNC9'],
            'VMC': ['Leadwell-1', 'Leadwell-2', 'AMS VMC-1', 'AMS VMC-2', 'AMS VMC-3', 'AMS VMC-4', 'HAAS-1', 'HAAS-2', 'IRUS'],
            'BANDSAW': ['BANDSAW-1', 'BANDSAW-2'],
            'TAPPING_GROUP': [
                'TAPPING MACHINE 1',
                'TAPPING MACHINE 2',
                'TAPPING MACHINE 3',
                'TAPPING MACHINE 4'
            ],
            'MILLING': [
                'MILLING MACHINE 1'
            ],
            'MANUAL_LATHE': [
                'MANUAL LATHE 1'
            ],
            'LATHE': [
                'MANUAL LATHE 1'
            ],
            'WELDING_GROUP': [
                'MIG WELDING',
                'TIG WELDING',
                'LASER WELDING',
                'ARC WELDING'
            ]
        }
        return machine_options.get(operation_type.upper(), [])

class TransferRequest(db.Model):
    __tablename__ = 'transfer_requests'
    
    id = db.Column(db.Integer, primary_key=True)
    route_operation_id = db.Column(db.Integer, db.ForeignKey('route_operations.id'))
    from_machine = db.Column(db.String(50))
    to_machine = db.Column(db.String(50))
    quantity = db.Column(db.Integer)
    sap_id = db.Column(db.String(50))
    status = db.Column(db.String(50), default='PENDING_LPI')  # PENDING_LPI, LPI_PASSED, LPI_FAILED
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime)
    
class LPIRecord(db.Model):
    __tablename__ = 'lpi_records'
    
    id = db.Column(db.Integer, primary_key=True)
    transfer_id = db.Column(db.Integer, db.ForeignKey('transfer_requests.id'))
    inspector = db.Column(db.String(50))
    inspection_date = db.Column(db.DateTime, default=datetime.utcnow)
    result = db.Column(db.String(50))  # PASS/FAIL
    remarks = db.Column(db.Text)
    
    # Quality parameters
    dimensions_ok = db.Column(db.Boolean)
    surface_finish_ok = db.Column(db.Boolean)
    critical_features_ok = db.Column(db.Boolean)
    measurements = db.Column(db.JSON)  # Store specific measurements 

class Drawing(db.Model):
    __tablename__ = 'drawings'
    
    drawing_number = db.Column(db.String(100), primary_key=True)
    sap_id = db.Column(db.String(100), nullable=False)
    total_quantity = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=datetime.now(timezone.utc))
    shipping_records = relationship("ShippingRecord", backref="drawing_rel")

    def __repr__(self):
        return f'<Drawing {self.drawing_number}>'

class ShippingRecord(db.Model):
    __tablename__ = 'shipping_records'
    
    id = db.Column(db.Integer, primary_key=True)
    drawing_number = db.Column(db.String(100), db.ForeignKey('drawings.drawing_number'), nullable=False)
    from_machine = db.Column(db.String(100), db.ForeignKey('machine.name'), nullable=False)
    to_machine = db.Column(db.String(100), db.ForeignKey('machine.name'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    notes = db.Column(db.String(500))
    date = db.Column(db.DateTime, nullable=False, default=datetime.now)
    
    # Relationships
    source_machine = db.relationship('Machine', foreign_keys=[from_machine], backref='outgoing_shipments')
    destination_machine = db.relationship('Machine', foreign_keys=[to_machine], backref='incoming_shipments')

    def __repr__(self):
        return f'<ShippingRecord {self.id}: {self.drawing_number} from {self.from_machine} to {self.to_machine}>'

class Batch(db.Model):
    __tablename__ = 'batch'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    drawing_id = db.Column(db.Integer, db.ForeignKey('machine_drawing.id'), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=datetime.now(timezone.utc))
    batch_quantity = db.Column(db.Integer, default=0)
    # Add relationship if needed 

class OperatorLogAction(db.Model):
    __tablename__ = 'operator_log_action'
    id = db.Column(db.Integer, primary_key=True)
    log_id = db.Column(db.Integer, db.ForeignKey('operator_log.id'), nullable=False)
    operator_name = db.Column(db.String(100), nullable=False)
    shift = db.Column(db.String(20), nullable=False)
    action_type = db.Column(db.String(50), nullable=False)
    quantity = db.Column(db.Integer, nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.now(timezone.utc))

    def __repr__(self):
        return f'<OperatorLogAction {self.id} {self.operator_name} {self.action_type}>' 