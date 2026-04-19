#!/bin/bash

# Doc-2-Markdown - macOS Launcher
echo "Starting Doc-2-Markdown Web App..."

# Navigate to script directory
cd "$(dirname "$0")"

# Create a virtual environment if it does not exist
if [ ! -d ".venv" ] && [ ! -d "venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv .venv
fi

# Activate virtual environment
if [ -d ".venv" ]; then
    source .venv/bin/activate
elif [ -d "venv" ]; then
    source venv/bin/activate
fi

# Upgrade pip
pip install --upgrade pip > /dev/null 2>&1

# Ensure requirements are installed
if ! python3 -c "import flask" &> /dev/null; then
    echo "Installing missing requirements..."
    pip install -r requirements.txt
fi

# Kill any existing process on port 5000 to prevent port collisions
lsof -ti:5000 | xargs kill -9 2>/dev/null

# Start the Flask backend server in the background
python3 app.py &
APP_PID=$!

# Wait a second for the server to spin up
sleep 1.5

# Open the default web browser to the local server
open "http://127.0.0.1:5000"

echo "Web App is running. Keep this window open or press CTRL+C to stop the server."

# Wait for process to end
wait $APP_PID
