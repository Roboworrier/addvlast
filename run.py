import os
import eventlet
import socket
eventlet.monkey_patch()

from app import app, socketio

def is_port_in_use(port):
    """Check if a port is in use"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(('0.0.0.0', port))
            return False
        except OSError:
            return True

def find_available_port(start_port, max_attempts=10):
    """Find an available port starting from start_port"""
    port = start_port
    for _ in range(max_attempts):
        if not is_port_in_use(port):
            return port
        port += 1
    raise RuntimeError(f"No available ports found after {max_attempts} attempts")

if __name__ == '__main__':
    default_port = int(os.environ.get('PORT', 5001))
    host = os.environ.get('HOST', '0.0.0.0')
    
    try:
        # Find an available port
        port = find_available_port(default_port)
        if port != default_port:
            app.logger.warning(f'Port {default_port} is in use, using port {port} instead')
        
        # Initialize the application
        app.logger.info('ChipSight staryestup')
        
        # Run the application with eventlet
        socketio.run(app, host=host, port=port, debug=True)
    except Exception as e:
        app.logger.error(f'Failed to start server: {str(e)}')
        raise 