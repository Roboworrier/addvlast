from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone
from sqlalchemy import func, case
from sqlalchemy.orm import relationship

# Initialize SQLAlchemy without binding to app
db = SQLAlchemy()

class Machine(db.Model):
    __tablename__ = 'machine'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    status = db.Column(db.String(50), nullable=False)
    production_records = relationship("ProductionRecord", back_populates="machine")
    drawing_assignments = relationship("MachineDrawingAssignment", back_populates="machine_rel", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'status': self.status
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
    setup_start_time = db.Column(db.DateTime)
    setup_end_time = db.Column(db.DateTime)
    first_cycle_start_time = db.Column(db.DateTime)
    last_cycle_end_time = db.Column(db.DateTime)
    current_status = db.Column(db.String(50))
    setup_time = db.Column(db.Float)
    cycle_time = db.Column(db.Float)
    abort_reason = db.Column(db.Text)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    batch_number = db.Column(db.String(50))
    run_planned_quantity = db.Column(db.Integer)
    run_completed_quantity = db.Column(db.Integer, default=0)
    run_rejected_quantity_fpi = db.Column(db.Integer, default=0)
    run_rejected_quantity_lpi = db.Column(db.Integer, default=0)
    run_rework_quantity_fpi = db.Column(db.Integer, default=0)
    run_rework_quantity_lpi = db.Column(db.Integer, default=0)
    quality_status = db.Column(db.String(20))
    operator_id = db.Column(db.String(50))
    machine_id = db.Column(db.String(50))
    shift = db.Column(db.String(20))
    fpi_status = db.Column(db.String(20), default='Pending')
    fpi_timestamp = db.Column(db.DateTime)
    fpi_inspector = db.Column(db.String(50))
    lpi_status = db.Column(db.String(20))
    lpi_timestamp = db.Column(db.DateTime)
    lpi_inspector = db.Column(db.String(50))
    production_hold_fpi = db.Column(db.Boolean, default=True)
    drawing_revision = db.Column(db.String(20))
    sap_id = db.Column(db.String(100))
    end_product_sap_id = db.Column(db.String(50), db.ForeignKey('end_product.sap_id'), nullable=True)
    operator_session_id = db.Column(db.Integer, db.ForeignKey('operator_session.id'))
    downtime_minutes = db.Column(db.Float, default=0)
    downtime_category = db.Column(db.String(20))

    end_product_sap_id_rel = relationship("EndProduct", back_populates="operator_logs_for_sap")
    drawing_rel = relationship("MachineDrawing", back_populates="operator_logs")
    quality_checks = relationship('QualityCheck', backref='operator_log', lazy=True)
    rework_items_sourced = relationship("ReworkQueue", foreign_keys='ReworkQueue.source_operator_log_id', back_populates="source_operator_log_rel")
    rework_attempt_for_queue = relationship("ReworkQueue", foreign_keys='ReworkQueue.assigned_operator_log_id', back_populates="assigned_operator_log_rel", uselist=False)
    scrapped_items = relationship("ScrapLog", foreign_keys='ScrapLog.operator_log_id', back_populates="operator_log_rel")

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
    login_time = db.Column(db.DateTime, default=datetime.now(timezone.utc))
    logout_time = db.Column(db.DateTime)
    is_active = db.Column(db.Boolean, default=True)
    shift = db.Column(db.String(20))
    
    machine_rel = relationship("Machine")
    operator_logs = relationship("OperatorLog", backref="operator_session")

class MachineBreakdownLog(db.Model):
    __tablename__ = 'machine_breakdown_log'
    id = db.Column(db.Integer, primary_key=True)
    machine_id = db.Column(db.Integer, db.ForeignKey('machine.id'), nullable=False)
    breakdown_start_time = db.Column(db.DateTime, nullable=False, default=datetime.now(timezone.utc))
    breakdown_end_time = db.Column(db.DateTime)
    reason = db.Column(db.Text)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.now(timezone.utc))
    
    machine_rel = relationship("Machine")

class MachineDrawingAssignment(db.Model):
    __tablename__ = 'machine_drawing_assignment'
    id = db.Column(db.Integer, primary_key=True)
    drawing_id = db.Column(db.Integer, db.ForeignKey('machine_drawing.id'), nullable=False)
    machine_id = db.Column(db.Integer, db.ForeignKey('machine.id'), nullable=False)
    assigned_quantity = db.Column(db.Integer, nullable=False)
    completed_quantity = db.Column(db.Integer, default=0)
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