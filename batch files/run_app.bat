@echo off
REM ==========================================================================
REM run_app.bat - Starts the PS Control GUI from source using the project .venv.
REM ==========================================================================

REM Switch to the folder this script is in (the project root).
cd /d "%~dp0"
REM Start main.py with the project virtual environment.
".venv\Scripts\python.exe" main.py
REM Keep the window open so the user can read the output.
pause
