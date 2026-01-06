# Create Desktop Shortcut for PDF-MD Converter
# This script creates a desktop shortcut for the PDF to Markdown converter

# Get the script's directory (where the app is installed)
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Get the current user's desktop path
$DesktopPath = [Environment]::GetFolderPath("Desktop")

# Define paths
$TargetFile = Join-Path $ScriptDir "pdf_to_markdown.py"
$VenvPython = Join-Path $ScriptDir ".venv\Scripts\pythonw.exe"
$ShortcutFile = Join-Path $DesktopPath "PDF-MD Converter.lnk"

# Check if virtual environment exists
if (-not (Test-Path $VenvPython)) {
    Write-Host "Virtual environment not found at: $VenvPython" -ForegroundColor Yellow
    Write-Host "Looking for system Python..."
    $VenvPython = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Path
    if (-not $VenvPython) {
        $VenvPython = (Get-Command python.exe -ErrorAction SilentlyContinue).Path
    }
    if (-not $VenvPython) {
        Write-Host "Python not found! Please install Python and try again." -ForegroundColor Red
        exit 1
    }
}

# Create the shortcut
$WScriptShell = New-Object -ComObject WScript.Shell
$Shortcut = $WScriptShell.CreateShortcut($ShortcutFile)
$Shortcut.TargetPath = $VenvPython
$Shortcut.Arguments = "`"$TargetFile`""
$Shortcut.WorkingDirectory = $ScriptDir
$Shortcut.Description = "PDF to Markdown Converter - AI-Enhanced with RAG Support"
$Shortcut.Save()

Write-Host "Shortcut created at: $ShortcutFile" -ForegroundColor Green
Write-Host "Target: $VenvPython" -ForegroundColor Cyan
Write-Host "Script: $TargetFile" -ForegroundColor Cyan
