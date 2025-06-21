# run.py

from app import app, socketio

# This file is used by Gunicorn to serve the app
# Example: gunicorn -k eventlet -w 1 -b 0.0.0.0:5001 run:app
