#!/bin/bash

APP_NAME="Doc-2-Markdown"
DMG_NAME="${APP_NAME}-macOS.dmg"
STAGING_DIR="build_staging"

echo "🧹 Cleaning up previous builds..."
rm -rf "$STAGING_DIR"
rm -f "$DMG_NAME"
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

# Rsync files specifically needed to run
rsync -aP \
    --exclude='.git' \
    --exclude='.venv' \
    --exclude='venv' \
    --exclude='build_staging' \
    --exclude='*.dmg' \
    --exclude='__pycache__' \
    --exclude='tests' \
    ./ "$APP_RESOURCES_DIR/"

# Make sure the startup script is executable in the bundle
chmod +x "$APP_RESOURCES_DIR/start_mac.sh"

echo "💿 Creating DMG Disk Image..."
hdiutil create -volname "$APP_NAME" -srcfolder "$STAGING_DIR/${APP_NAME}.app" -ov -format UDZO "$DMG_NAME"

echo "✨ Done! Installer created at: $(pwd)/$DMG_NAME"
# Clean up staging dir
rm -rf "$STAGING_DIR"
