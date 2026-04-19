#!/bin/bash

# Doc-2-Markdown - macOS Launcher
echo "Starting Doc-2-Markdown Web App..."

# Navigate to script directory
cd "$(dirname "$0")"

# Activate virtual environment if it exists
if [ -d ".venv" ]; then
    echo "Activating virtual environment..."
    source .venv/bin/activate
elif [ -d "venv" ]; then
    echo "Activating virtual environment..."
    source venv/bin/activate
else
    echo "Warning: No virtual environment found. Make sure dependencies are installed."
fi

# Ensure requirements are installed
if ! python3 -c "import flask" &> /dev/null; then
    echo "Installing missing requirements (flask)..."
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

echo "Web App is running. Press CTRL+C to stop the server."

# Wait for process to end
wait $APP_PID
