@echo off
cd /d "%~dp0"
set PATH=%PATH%;F:\script
echo ========================================
echo  OpenClaw Voice Control - Windows
echo ========================================
echo.
echo  Starting voice service...
echo.
"E:\Program files\Python\Python311\python.exe" -m openclaw_voice_control --config config/default.yaml --env-file .env
if errorlevel 1 (
    echo. & echo [!] Service exited with code %errorlevel%
    pause
)
