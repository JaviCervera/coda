@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%.."
set "PYTHONPATH=%PROJECT_ROOT%;%PYTHONPATH%"
python -m codac %*
endlocal
exit /b %ERRORLEVEL%
