@echo off
chcp 65001 >nul
cd /d "%~dp0"
title AI 電商影片自動中文化 - 工作台（關閉此視窗即停止服務）
if not exist ".venv\Scripts\python.exe" (
  echo 尚未安裝，請先雙擊 install_windows.bat
  pause
  exit /b
)
echo ==================================================
echo   AI 電商影片自動中文化 工作台 啟動中...
echo   本機使用：  http://localhost:8000
echo   同事使用（同一個網路）：http://這台電腦的IP:8000
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do for /f "tokens=*" %%b in ("%%a") do echo        http://%%b:8000
echo   使用中請不要關閉這個視窗。
echo ==================================================
start "" /b cmd /c "timeout /t 5 >nul & start http://localhost:8000"
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
pause
