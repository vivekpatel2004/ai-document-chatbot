@echo off
chcp 65001 >nul
title Document Chatbot Backend Server
color 0A
echo ========================================
echo   Document Chatbot Backend Server
echo ========================================
echo.
cd /d "%~dp0backend"
echo Current directory: %CD%
echo.
echo Checking for .env file...
if not exist ".env" (
    echo WARNING: .env file not found!
    echo Please create .env file with GOOGLE_API_KEY
    echo.
)
echo.
echo Starting FastAPI server...
echo Server will be available at: http://127.0.0.1:8000
echo Press CTRL+C to stop the server
echo.
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
echo Selecting the backend Python environment...
if exist "venv\Scripts\python.exe" (
    set "BACKEND_PYTHON=venv\Scripts\python.exe"
) else (
    where py >nul 2>nul
    if not errorlevel 1 (
        set "BACKEND_PYTHON=py"
    ) else (
        set "BACKEND_PYTHON=python"
    )
)
%BACKEND_PYTHON% --version
echo.
echo Checking backend dependencies, including the Turso DB-API driver...
%BACKEND_PYTHON% -c "import fastapi, turso_serverless" 2>nul
if errorlevel 1 (
    echo Installing dependencies from backend\requirements.txt...
    %BACKEND_PYTHON% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo ERROR: Dependency installation failed.
        pause
        exit /b 1
    )
    echo.
)
echo.
%BACKEND_PYTHON% -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause

