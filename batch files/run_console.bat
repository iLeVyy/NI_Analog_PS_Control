@echo off
REM ==========================================================================
REM run_console.bat - Runs the console check of PS Control (no GUI):
REM prints the configuration, scans devices, sets up the AO channels, closes.
REM ==========================================================================

REM Switch to the folder this script is in (the project root).
cd /d "%~dp0"
REM Start main.py in console mode with the project virtual environment.
".venv\Scripts\python.exe" main.py --console
REM Keep the window open so the user can read the output.
pause
