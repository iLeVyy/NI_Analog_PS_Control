@echo off
REM ==========================================================================
REM setup_venv.bat - Creates the Python virtual environment (.venv) for
REM PS Control and installs the packages from requirements.txt.
REM LabVIEW analogy: like installing the run-time and toolkits for a project.
REM ==========================================================================

REM Switch to the folder this script is in (the project root).
cd /d "%~dp0"
REM Create the virtual environment folder ".venv" using the installed Python.
python -m venv .venv
REM Stop with a message if the environment could not be created.
if errorlevel 1 (echo ERROR: Could not create .venv - is Python installed and on PATH? & pause & exit /b 1)
REM Use the Brose package index for pip downloads.
set "PIP_TRUSTED_HOST=autodevpi.brose.net"
set "PIP_INDEX_URL=https://autodevpi.brose.net/packages/stable"
REM Upgrade pip inside the environment.
".venv\Scripts\python.exe" -m pip install --upgrade pip
REM Install all project packages.
".venv\Scripts\python.exe" -m pip install -r requirements.txt
REM Stop with a message if the installation failed.
if errorlevel 1 (echo ERROR: Package installation failed. & pause & exit /b 1)
REM Report success.
echo .venv ready.
REM Keep the window open so the user can read the result.
pause
