@echo off
REM ======================================================================
REM build_nuitka.bat - Build PS Control as a Nuitka standalone application.
REM The completed package is published to build\dist\PS_Control.
REM ======================================================================

REM Keep build variables local to this script.
setlocal
REM Switch to the project root (this script is stored there).
cd /d "%~dp0"
REM Stop if the project root could not be selected.
if errorlevel 1 goto :root_failed
REM Confirm the canonical application version file is present.
if not exist "version.txt" goto :version_missing
REM Read the application version for the build banner.
set /p APPVER=<version.txt
REM Set the generated-artifact root.
set "DIST_ROOT=build\dist"
REM Set an isolated staging directory so failed builds do not replace the published package.
set "STAGING_ROOT=%DIST_ROOT%\staging"
REM Set the final standalone package directory.
set "FINAL_ROOT=%DIST_ROOT%\PS_Control"
REM Clear the discovered Nuitka output folder until the build completes.
set "OUTPUT_ROOT="
REM Show which application version is being built.
echo Building PS Control %APPVER% ...
REM Remove any previous temporary build so stale files cannot enter this package.
if exist "%STAGING_ROOT%" rmdir /S /Q "%STAGING_ROOT%"
REM Stop if the previous staging directory could not be removed.
if errorlevel 1 goto :staging_cleanup_failed
REM Create the clean staging directory.
mkdir "%STAGING_ROOT%"
REM Stop if the staging directory could not be created.
if errorlevel 1 goto :staging_create_failed
REM Set the executable filename so Nuitka records PS_Control.exe as OriginalFilename.
REM Append a fourth component because Windows file versions use up to four numeric parts.
REM Compile into a staging directory; Nuitka names the inner distribution directory from the entry script.
".venv\Scripts\python.exe" -m nuitka --standalone --enable-plugin=pyside6 --include-module=ui.app --include-module=core.simulation --include-module=core.nidaqmx_backend --include-package=nidaqmx --output-dir="%STAGING_ROOT%" --output-filename=PS_Control.exe --file-version=%APPVER%.0 main.py
REM Stop if Nuitka compilation failed.
if errorlevel 1 goto :compile_failed
REM Select the distribution directory name Nuitka produced for main.py.
if exist "%STAGING_ROOT%\main.dist\PS_Control.exe" set "OUTPUT_ROOT=%STAGING_ROOT%\main.dist"
REM Also accept Nuitka versions that derive the distribution directory from the output filename.
if not defined OUTPUT_ROOT if exist "%STAGING_ROOT%\PS_Control.dist\PS_Control.exe" set "OUTPUT_ROOT=%STAGING_ROOT%\PS_Control.dist"
REM Stop if the expected executable was not produced.
if not defined OUTPUT_ROOT goto :output_missing
REM Copy the default settings template beside the executable.
xcopy /E /I /Y "config\defaults" "%OUTPUT_ROOT%\config\defaults" >NUL
REM Stop if copying the default settings failed.
if errorlevel 1 goto :copy_failed
REM Copy the runtime catalog files beside the executable.
xcopy /E /I /Y "config\presets" "%OUTPUT_ROOT%\config\presets" >NUL
REM Stop if copying the runtime catalogs failed.
if errorlevel 1 goto :copy_failed
REM Copy the simulated device list beside the executable.
xcopy /E /I /Y "config\simulation" "%OUTPUT_ROOT%\config\simulation" >NUL
REM Stop if copying the simulation data failed.
if errorlevel 1 goto :copy_failed
REM Copy the Qt Designer forms loaded by the application at runtime.
xcopy /I /Y "ui\*.ui" "%OUTPUT_ROOT%\ui\" >NUL
REM Stop if copying the Qt Designer forms failed.
if errorlevel 1 goto :copy_failed
REM Copy the example and user sequence files shipped with the application.
xcopy /E /I /Y "sequences" "%OUTPUT_ROOT%\sequences" >NUL
REM Stop if copying the sequence files failed.
if errorlevel 1 goto :copy_failed
REM Copy theme and icon resources beside the executable.
xcopy /E /I /Y "resources" "%OUTPUT_ROOT%\resources" >NUL
REM Stop if copying the application resources failed.
if errorlevel 1 goto :copy_failed
REM Copy the canonical version file beside the executable.
copy /Y "version.txt" "%OUTPUT_ROOT%\version.txt" >NUL
REM Stop if copying the version file failed.
if errorlevel 1 goto :copy_failed
REM Remove a previous published package only after the new staged package is complete.
if exist "%FINAL_ROOT%" rmdir /S /Q "%FINAL_ROOT%"
REM Stop if the previous published package could not be removed.
if errorlevel 1 goto :publish_failed
REM Publish the completed standalone directory with the requested name.
move "%OUTPUT_ROOT%" "%FINAL_ROOT%" >NUL
REM Stop if the completed package could not be moved into place.
if errorlevel 1 goto :publish_failed
REM Remove the remaining staging files and intermediate compiler output.
if exist "%STAGING_ROOT%" rmdir /S /Q "%STAGING_ROOT%"
REM Warn if staging cleanup failed without misreporting the published build.
if exist "%STAGING_ROOT%" echo WARNING: Could not remove temporary build files in %STAGING_ROOT%.
REM Remove stale output from the previous build layout when present.
if exist "%DIST_ROOT%\main.dist" rmdir /S /Q "%DIST_ROOT%\main.dist"
REM Remove stale compiler intermediates from the previous build layout when present.
if exist "%DIST_ROOT%\main.build" rmdir /S /Q "%DIST_ROOT%\main.build"
REM Report the final executable and its containing package folder.
echo Build ready: %FINAL_ROOT%\PS_Control.exe
REM Keep the window open so the user can read the result.
pause
REM Return success to the calling shell.
exit /b 0

REM Report that the project root could not be selected.
:root_failed
echo ERROR: Could not switch to the project root.
goto :failed

REM Report that the canonical application version file is missing.
:version_missing
echo ERROR: version.txt was not found in the project root.
goto :failed

REM Report that old staging files could not be removed.
:staging_cleanup_failed
echo ERROR: Could not clean the temporary build directory.
goto :failed

REM Report that the temporary build directory could not be created.
:staging_create_failed
echo ERROR: Could not create the temporary build directory.
goto :failed

REM Report a Nuitka compilation failure.
:compile_failed
echo ERROR: Nuitka build failed.
goto :failed

REM Report that no expected standalone directory was produced.
:output_missing
echo ERROR: Nuitka did not produce PS_Control.exe in the staging directory.
goto :failed

REM Report any missing or failed runtime asset copy.
:copy_failed
echo ERROR: Could not copy all runtime files into the standalone package.
goto :failed

REM Report that the staged application could not be published.
:publish_failed
echo ERROR: Could not publish the standalone package to %FINAL_ROOT%.
goto :failed

REM Keep the window open after an error and return a failure code.
:failed
pause
exit /b 1