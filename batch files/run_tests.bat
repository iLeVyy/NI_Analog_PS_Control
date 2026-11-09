@echo off
REM ==========================================================================
REM run_tests.bat - Runs all unit tests in the tests folder with pytest.
REM ==========================================================================

REM Switch to the folder this script is in (the project root).
cd /d "%~dp0"
REM Run pytest with the project virtual environment, short output.
".venv\Scripts\python.exe" -m pytest -q
REM Keep the window open so the user can read the result.
pause
