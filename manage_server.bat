@echo off
setlocal enabledelayedexpansion

if "%1"=="" (
    echo Usage: manage_server.bat [start^|stop^|restart]
    exit /b 1
)

set COMMAND=%1
set PYTHON_CMD=python

REM Check if Python is available
where !PYTHON_CMD! >nul 2>&1
if !ERRORLEVEL! neq 0 (
    echo Python not found in PATH
    exit /b 1
)

REM Execute the command
!PYTHON_CMD! manage_server.py !COMMAND!

if !ERRORLEVEL! neq 0 (
    echo Failed to !COMMAND! server
    exit /b 1
)

echo Server !COMMAND! completed successfully
exit /b 0 