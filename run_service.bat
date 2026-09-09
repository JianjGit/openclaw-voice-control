@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo  OpenClaw Voice Core - Windows
echo ========================================
echo.

if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
) else (
    set "PYTHON=python"
)

"%PYTHON%" -m openclaw_voice_control --config config/default.yaml --env-file .env %*
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
    echo.
    echo [!] Service exited with code %EXIT_CODE%
)
exit /b %EXIT_CODE%
