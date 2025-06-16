import os
import eventlet
eventlet.monkey_patch()

from app import app, socketio

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))  # Use port 5001 as default
    host = os.environ.get('HOST', '0.0.0.0')  # Use localhost as default
    
    # Initialize the application
    app.logger.info('ChipSight startup')
    
    # Run the application with eventlet
    socketio.run(app, host=host, port=port, debug=True) 