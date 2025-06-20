from flask import Flask
from models import db, Machine, EndProduct, ProductionRecord, User, Project
from flask_migrate import Migrate
from datetime import datetime, timedelta, timezone
from werkzeug.security import generate_password_hash
import random
import os
import logging
from logging.handlers import RotatingFileHandler

# Configure logging
if not os.path.exists('logs'):
    os.makedirs('logs')

logger = logging.getLogger('init_db')
logger.setLevel(logging.INFO)
handler = RotatingFileHandler('logs/init_db.log', maxBytes=1024 * 1024, backupCount=5)
handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s'))
logger.addHandler(handler)

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
        print("Created sample projects")
        logger.info("Created sample projects")

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
        logger.info("Created sample end products")

        # Create production records for the last 7 days
        machines = Machine.query.all()
        records_created = 0
        for machine in machines:
            current_time = end_time - timedelta(days=7)
            while current_time < end_time:
                try:
                    # Create a record every 2 hours
                    record = ProductionRecord(
                        machine_id=machine.id,
                        end_product_id=random.choice(products).id,
                        quantity=random.randint(5, 20),
                        timestamp=current_time,
                        status='completed'
                    )
                    db.session.add(record)
                    records_created += 1
                    current_time += timedelta(hours=2)
                except Exception as e:
                    logger.error(f"Error creating production record: {str(e)}")
                    db.session.rollback()
                    continue

        db.session.commit()
        logger.info(f"Created {records_created} sample production records")
    except Exception as e:
        logger.error(f"Error creating sample data: {str(e)}")
        db.session.rollback()
        raise

def init_db():
    try:
        # Create a new Flask app instance
        app = Flask(__name__)
        
        # Ensure instance directory exists
        instance_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance')
        if not os.path.exists(instance_path):
            os.makedirs(instance_path)
            logger.info(f"Created instance directory at {instance_path}")
        
        # Configure the app
        app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{os.path.join(instance_path, "chipsight.db")}'
        app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
        
        # Initialize SQLAlchemy with the app
        db.init_app(app)
        
        # Initialize Flask-Migrate
        migrate = Migrate(app, db)
        
        with app.app_context():
            logger.info("Starting database initialization")
            
            # Drop all tables
            db.drop_all()
            logger.info("Dropped all existing tables")
            
            # Create all tables with the new schema
            db.create_all()
            logger.info("Created all tables with new schema")
            
            # Create default admin user
            admin_user = User(
                username='admin',
                password=generate_password_hash('admin123'),
                role='admin',
                is_active=True
            )
            try:
                db.session.add(admin_user)
                db.session.commit()
                logger.info("Created default admin user")
            except Exception as e:
                logger.error(f"Error creating admin user: {str(e)}")
                db.session.rollback()
                raise
            
            # Create default machines with machine_type
            machines = [
                # VMC
                ('Leadwell-1', 'VMC'), ('Leadwell-2', 'VMC'),
                ('AMS VMC-1', 'VMC'), ('AMS VMC-2', 'VMC'),
                ('AMS VMC-3', 'VMC'), ('AMS VMC-4', 'VMC'),
                ('HAAS-1', 'VMC'), ('HAAS-2', 'VMC'), ('IRUS', 'VMC'),
                # CNC
                ('CNC1', 'CNC'), ('CNC2', 'CNC'), ('CNC3', 'CNC'), ('CNC4', 'CNC'),
                ('CNC5', 'CNC'), ('CNC6', 'CNC'), ('CNC7', 'CNC'), ('CNC8', 'CNC'), ('CNC9', 'CNC'),
                # BANDSAW
                ('BANDSAW-1', 'BANDSAW'), ('BANDSAW-2', 'BANDSAW')
            ]
            
            # Add all machines
            machines_added = 0
            for name, mtype in machines:
                if not Machine.query.filter_by(name=name).first():
                    db.session.add(Machine(name=name, status='available', machine_type=mtype))
                    machines_added += 1
                    logger.info(f"Added machine: {name} ({mtype})")
                else:
                    logger.info(f"Machine {name} ({mtype}) already exists")
            
            try:
                db.session.commit()
                logger.info(f"Successfully initialized database with {machines_added} machines")
                
                # Create sample data
                create_sample_data()
                logger.info("Successfully created sample data")
            except Exception as e:
                logger.error(f"Error committing changes: {str(e)}")
                db.session.rollback()
                raise
            
    except Exception as e:
        logger.error(f"Database initialization failed: {str(e)}")
        raise

if __name__ == '__main__':
    try:
        init_db()
        logger.info("Database initialization completed successfully")
    except Exception as e:
        logger.error(f"Database initialization script failed: {str(e)}")
        raise 