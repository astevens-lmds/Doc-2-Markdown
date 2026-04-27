#!/bin/bash

set -e

# Always run from the directory this script lives in so relative paths
# (pdf_to_markdown.py, requirements.txt, frontend/, etc.) resolve correctly
# regardless of where the script is invoked from.
cd "$(dirname "$0")"

APP_NAME="Doc-2-Markdown"
VERSION=$(python3 -c "import re; m=re.search(r'__version__\s*=\s*\"([^\"]+)\"', open('pdf_to_markdown.py').read()); print(m.group(1) if m else 'dev')")
DMG_NAME="${APP_NAME}-${VERSION}-macOS.dmg"
STAGING_DIR="build_staging"

# Sanity check: bundled launcher and core deps must exist before we ship.
for required in start_mac.sh app.py pdf_to_markdown.py requirements.txt frontend/index.html; do
    if [ ! -e "$required" ]; then
        echo "❌ Missing required file: $required" >&2
        exit 1
    fi
done

echo "🧹 Cleaning up previous builds (building version ${VERSION})..."
rm -rf "$STAGING_DIR"
# Remove ALL prior DMGs in this dir (un-versioned legacy + prior versions)
rm -f "${APP_NAME}"*.dmg
mkdir -p "$STAGING_DIR"

echo "🍎 Building AppleScript Wrapper App..."
cat << 'EOF' > "$STAGING_DIR/launcher.applescript"
set app_path to POSIX path of (path to me)
set script_path to app_path & "Contents/Resources/app/start_mac.sh"
tell application "Terminal"
    do script quoted form of script_path
    activate
end tell
EOF

osacompile -o "$STAGING_DIR/${APP_NAME}.app" "$STAGING_DIR/launcher.applescript"

echo "📦 Bundling application files..."
APP_RESOURCES_DIR="$STAGING_DIR/${APP_NAME}.app/Contents/Resources/app"
mkdir -p "$APP_RESOURCES_DIR"

# Rsync files specifically needed to run.
# config.json and usage.json are user-generated at runtime and may contain
# real API keys / spend history — exclude them so the DMG ships clean.
rsync -aP \
    --exclude='.git' \
    --exclude='.venv' \
    --exclude='venv' \
    --exclude='build_staging' \
    --exclude='*.dmg' \
    --exclude='__pycache__' \
    --exclude='.DS_Store' \
    --exclude='tests' \
    --exclude='config.json' \
    --exclude='usage.json' \
    ./ "$APP_RESOURCES_DIR/"

# Make sure the startup script is executable in the bundle
chmod +x "$APP_RESOURCES_DIR/start_mac.sh"

echo "💿 Creating DMG Disk Image..."
hdiutil create -volname "$APP_NAME" -srcfolder "$STAGING_DIR/${APP_NAME}.app" -ov -format UDZO "$DMG_NAME"

echo "✨ Done! Installer created at: $(pwd)/$DMG_NAME"
# Clean up staging dir
rm -rf "$STAGING_DIR"
