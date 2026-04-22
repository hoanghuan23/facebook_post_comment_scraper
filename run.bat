@echo off
REM Run Facebook Scraper API on Windows

REM Activate virtual environment
if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
)

REM Check if .env exists
if not exist .env (
    echo WARNING: .env file not found. Creating from .env.example...
    copy .env.example .env
    echo Please edit .env with your configuration
    exit /b 1
)

REM Install dependencies
echo Installing dependencies...
pip install -r requirements.txt

REM Initialize database
echo Initializing database...
python -c "from backend.database.db import init_db; init_db()"

REM Run the application
echo Starting Facebook Scraper API...
echo API Documentation: http://localhost:8000/api/docs
uvicorn backend.api.main:app --reload --host 0.0.0.0 --port 8000
