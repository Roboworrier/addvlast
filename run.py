import os
import eventlet
eventlet.monkey_patch()

from app import app, socketio

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    host = os.environ.get('HOST', '127.0.0.1')

    try:
        app.logger.info('ChipSight startup on fixed port %d', port)
        socketio.run(app, host=host, port=port, debug=True)
    except Exception as e:
        app.logger.error(f'Failed to start server: {str(e)}')
        raise
