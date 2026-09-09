@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ========================================
echo  OpenClaw Voice Control - 浮层 UI
echo ========================================
echo.
echo  启动浮层窗口（可选）...
echo.
"E:\Program files\Python\Python311\python.exe" -m openclaw_voice_control.overlay_app --config config/default.yaml --env-file .env
pause
