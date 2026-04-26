#!/bin/bash

# Doc-2-Markdown - macOS Launcher
# Works whether invoked from a source checkout, /Applications, or a
# read-only DMG mount. Venv and runtime data live in the user's
# Application Support directory so nothing tries to write into the
# (possibly read-only) app bundle.

set -e

echo "Starting Doc-2-Markdown Web App..."

# Directory of this script — where the bundled Python code lives.
BUNDLE_DIR="$(cd "$(dirname "$0")" && pwd)"

# Writable data dir for venv, config.json, usage.json, prompts, etc.
APP_SUPPORT="$HOME/Library/Application Support/Doc-2-Markdown"
VENV_DIR="$APP_SUPPORT/.venv"
mkdir -p "$APP_SUPPORT"

# Port — default 5005. 5000 is reserved on recent macOS for AirPlay Receiver
# (Control Center) and will return HTTP 403 if AirPlay Receiver is enabled.
PORT="${DOC2MD_PORT:-5005}"

# First-run venv creation in the user-writable location.
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating Python virtual environment (one-time setup)..."
    python3 -m venv "$VENV_DIR"
fi

# Activate the venv.
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# Ensure pip is current and deps are installed. Keeping this cheap on warm
# starts — only re-install when flask is missing.
python3 -m pip install --upgrade pip > /dev/null 2>&1 || true
if ! python3 -c "import flask" &> /dev/null; then
    echo "Installing requirements (one-time setup)..."
    python3 -m pip install -r "$BUNDLE_DIR/requirements.txt"
fi

# Clear any leftover server on our port.
lsof -ti:"$PORT" | xargs kill -9 2>/dev/null || true

# Tell the Python app where to put runtime data and which port to bind.
export DOC2MD_DATA_DIR="$APP_SUPPORT"
export DOC2MD_PORT="$PORT"

# Run from the writable dir so relative side-effects land somewhere safe.
cd "$APP_SUPPORT"
python3 "$BUNDLE_DIR/app.py" &
APP_PID=$!

sleep 1.5
open "http://127.0.0.1:$PORT"

echo "Web App is running on http://127.0.0.1:$PORT"
echo "Keep this window open or press CTRL+C to stop the server."

wait $APP_PID
