@echo off
cd /d "%~dp0"
if exist "dist\Saywell\Saywell.exe" (
  start "" "dist\Saywell\Saywell.exe" --background
) else (
  start "" ".venv\Scripts\pythonw.exe" "scripts\run_app.py" --background
)
