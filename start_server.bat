@echo off
echo Starting ChipSight Server with HTTPS...
call venv\Scripts\activate
set FLASK_APP=app.py
set FLASK_ENV=production
set FLASK_DEBUG=0
python -m flask run --host=0.0.0.0 --port=8443 --cert=cert/cert.pem --key=cert/key.pem
pause 