#!/bin/bash
# Run Facebook Scraper API

# Activate virtual environment if it exists
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Load environment
if [ ! -f ".env" ]; then
    echo "⚠️  .env file not found. Creating from .env.example..."
    cp .env.example .env
    echo "📝 Please edit .env with your configuration"
    exit 1
fi

# Install dependencies
echo "📦 Installing dependencies..."
pip install -r requirements.txt

# Initialize database
echo "🗄️  Initializing database..."
python -c "from backend.database.db import init_db; init_db()"

# Run the application
echo "🚀 Starting Facebook Scraper API..."
echo "📊 API Documentation: http://localhost:8000/api/docs"
uvicorn backend.api.main:app --reload --host 0.0.0.0 --port 8000
