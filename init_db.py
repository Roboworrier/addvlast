from flask import Flask
from models import db, Machine, EndProduct, ProductionRecord, User, Project
from flask_migrate import Migrate
from datetime import datetime, timedelta, timezone
from werkzeug.security import generate_password_hash
import random
import os

def create_sample_data():
    """Create sample data for testing"""
    try:
        # Create sample projects
        projects = [
            Project(project_code='PRJ001', project_name='Engine Block Production'),
            Project(project_code='PRJ002', project_name='Transmission Housing')
        ]
        db.session.add_all(projects)
        db.session.commit()

        # Create sample end products
        end_time = datetime.now(timezone.utc)
        products = [
            EndProduct(
                sap_id='SAP001',
                name='Engine Block Type A',
                project_id=projects[0].id,
                quantity=100,
                completion_date=end_time + timedelta(days=30),
                setup_time_std=45,
                cycle_time_std=15,
                is_first_piece_fpi_required=True,
                is_last_piece_lpi_required=True
            ),
            EndProduct(
                sap_id='SAP002',
                name='Transmission Case',
                project_id=projects[1].id,
                quantity=150,
                completion_date=end_time + timedelta(days=45),
                setup_time_std=60,
                cycle_time_std=20,
                is_first_piece_fpi_required=True,
                is_last_piece_lpi_required=True
            )
        ]
        db.session.add_all(products)
        db.session.commit()

        # Create production records for the last 7 days
        machines = Machine.query.all()
        for machine in machines:
            current_time = end_time - timedelta(days=7)
            while current_time < end_time:
                # Create a record every 2 hours
                record = ProductionRecord(
                    machine_id=machine.id,
                    end_product_id=random.choice(products).id,
                    quantity=random.randint(5, 20),
                    timestamp=current_time,
                    cycle_time=random.randint(30, 60),
                    status='completed'
                )
                db.session.add(record)
                current_time += timedelta(hours=2)

        db.session.commit()
    except Exception as e:
        print(f"Error creating sample data: {str(e)}")
        db.session.rollback()

def init_db():
    # Create a new Flask app instance
    app = Flask(__name__)
    
    # Ensure instance directory exists
    instance_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance')
    if not os.path.exists(instance_path):
        os.makedirs(instance_path)
    
    # Configure the app
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{os.path.join(instance_path, "chipsight.db")}'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    
    # Initialize SQLAlchemy with the app
    db.init_app(app)
    
    # Initialize Flask-Migrate
    migrate = Migrate(app, db)
    
    with app.app_context():
        # Drop all tables
        db.drop_all()
        
        # Create all tables with the new schema
        db.create_all()
        
        # Create default machines
        machines = [
            Machine(name='Leadwell-1', status='available'),
            Machine(name='Leadwell-2', status='available')
        ]
        db.session.add_all(machines)
        
        # Create default users
        users = [
            User(username='admin', password=generate_password_hash('admin123'), role='admin', is_active=True),
            User(username='manager', password=generate_password_hash('manager123'), role='manager', is_active=True),
            User(username='planner', password=generate_password_hash('planner123'), role='planner', is_active=True),
            User(username='plant_head', password=generate_password_hash('plant123'), role='plant_head', is_active=True),
            User(username='quality', password=generate_password_hash('quality123'), role='quality', is_active=True)
        ]
        db.session.add_all(users)
        db.session.commit()
        
        # Create sample data
        create_sample_data()
        
        print("Database initialized successfully with sample data")

if __name__ == '__main__':
    init_db() 