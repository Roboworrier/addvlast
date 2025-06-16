@echo off
title Starting ChipSight Server...

REM Check if virtual environment exists
IF EXIST venv\Scripts\activate.bat (
    echo Activating virtual environment...
    call venv\Scripts\activate.bat
) ELSE (
    echo [ERROR] Virtual environment not found. Make sure 'venv' exists in this directory.
    pause
    exit /b
)

REM Run the server using Waitress
echo Starting Waitress on http://0.0.0.0:5000 ...
waitress-serve --host=0.0.0.0 --port=5000 app:app

REM If waitress fails, keep window open to read error
IF %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Server failed to start. Check if 'app.py' exists and contains 'app = Flask(__name__)'.
    pause
)
