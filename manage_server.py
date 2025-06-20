import os
import sys
import psutil
import signal
import time
import logging
from logging.handlers import RotatingFileHandler

# Configure logging
if not os.path.exists('logs'):
    os.makedirs('logs')

logger = logging.getLogger('server_manager')
logger.setLevel(logging.INFO)
handler = RotatingFileHandler('logs/server_manager.log', maxBytes=1024 * 1024, backupCount=5)
handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s'))
logger.addHandler(handler)

def find_python_process_on_port(port):
    """Find Python process using the specified port"""
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            # Check if it's a Python process
            if proc.info['name'] and 'python' in proc.info['name'].lower():
                connections = proc.connections()
                for conn in connections:
                    if conn.laddr.port == port:
                        return proc
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return None

def kill_process_on_port(port):
    """Kill process using the specified port"""
    process = find_python_process_on_port(port)
    if process:
        try:
            process.terminate()
            logger.info(f"Terminated process {process.pid} on port {port}")
            # Wait for the process to terminate
            process.wait(timeout=5)
        except psutil.TimeoutExpired:
            # Force kill if graceful termination fails
            process.kill()
            logger.warning(f"Force killed process {process.pid} on port {port}")
        except Exception as e:
            logger.error(f"Error killing process on port {port}: {str(e)}")
            return False
        return True
    return False

def cleanup_port(port):
    """Ensure port is available by cleaning up any existing processes"""
    if kill_process_on_port(port):
        # Wait a moment for the port to be fully released
        time.sleep(2)
        return True
    return False

def start_server():
    """Start the server process"""
    try:
        # Default port
        port = 5001
        
        # Check if port is in use
        process = find_python_process_on_port(port)
        if process:
            logger.warning(f"Port {port} is in use by process {process.pid}")
            if cleanup_port(port):
                logger.info(f"Successfully cleaned up port {port}")
            else:
                logger.error(f"Failed to clean up port {port}")
                return False
        
        # Start the server
        os.system('python run.py')
        logger.info("Server started successfully")
        return True
    except Exception as e:
        logger.error(f"Error starting server: {str(e)}")
        return False

def stop_server():
    """Stop the server process"""
    try:
        port = 5001
        if cleanup_port(port):
            logger.info("Server stopped successfully")
            return True
        logger.warning("No server process found to stop")
        return True
    except Exception as e:
        logger.error(f"Error stopping server: {str(e)}")
        return False

def restart_server():
    """Restart the server process"""
    stop_server()
    time.sleep(2)  # Wait for port to be fully released
    return start_server()

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python manage_server.py [start|stop|restart]")
        sys.exit(1)
    
    command = sys.argv[1].lower()
    success = False
    
    if command == 'start':
        success = start_server()
    elif command == 'stop':
        success = stop_server()
    elif command == 'restart':
        success = restart_server()
    else:
        print("Invalid command. Use 'start', 'stop', or 'restart'")
        sys.exit(1)
    
    if not success:
        sys.exit(1) 