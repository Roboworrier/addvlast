#!/usr/bin/env python3
"""
Test script for machine shop functionality
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app import app, db
from models import Machine, ProcessRoute, RouteOperation
from route_management import route_bp

def test_machine_shop():
    """Test the machine shop functionality"""
    with app.app_context():
        print("Testing Machine Shop Functionality...")
        
        # Test 1: Check if machines exist
        print("\n1. Checking machines in database...")
        machines = Machine.query.all()
        print(f"Found {len(machines)} machines:")
        for machine in machines:
            print(f"  - {machine.name} ({machine.machine_type}) - Status: {machine.status}")
        
        # Test 2: Check machine groups
        print("\n2. Checking machine groups...")
        tapping_machines = [m for m in machines if 'TAP' in m.name.upper()]
        welding_machines = [m for m in machines if 'WELD' in m.name.upper()]
        milling_machines = [m for m in machines if 'MILL' in m.name.upper()]
        lathe_machines = [m for m in machines if 'LATHE' in m.name.upper()]
        
        print(f"Tapping machines: {len(tapping_machines)}")
        print(f"Welding machines: {len(welding_machines)}")
        print(f"Milling machines: {len(milling_machines)}")
        print(f"Lathe machines: {len(lathe_machines)}")
        
        # Test 3: Check route operations
        print("\n3. Checking route operations...")
        routes = ProcessRoute.query.all()
        print(f"Found {len(routes)} routes:")
        for route in routes:
            print(f"  - Route {route.id}: {route.sap_id} (Status: {route.status})")
            operations = RouteOperation.query.filter_by(route_id=route.id).all()
            for op in operations:
                print(f"    * Operation {op.sequence}: {op.operation_type} -> {op.assigned_machine}")
        
        # Test 4: Test machine validation
        print("\n4. Testing machine validation...")
        from models import RouteOperation
        
        test_cases = [
            ('CNC', 'CNC1'),
            ('VMC', 'Leadwell-1'),
            ('TAPPING_GROUP', 'TAPPING MACHINE 1'),
            ('WELDING_GROUP', 'MIG WELDING'),
            ('MILLING', 'MILLING MACHINE 1'),
            ('LATHE', 'MANUAL LATHE 1')
        ]
        
        for operation_type, machine_name in test_cases:
            is_valid = RouteOperation.validate_machine_assignment(operation_type, machine_name)
            print(f"  {operation_type} -> {machine_name}: {'✓' if is_valid else '✗'}")
        
        # Test 5: Test available machines
        print("\n5. Testing available machines...")
        for operation_type in ['CNC', 'VMC', 'TAPPING_GROUP', 'WELDING_GROUP', 'MILLING', 'LATHE']:
            available = RouteOperation.get_available_machines(operation_type)
            print(f"  {operation_type}: {len(available)} machines available")
        
        print("\nMachine shop test completed!")

if __name__ == '__main__':
    test_machine_shop() 