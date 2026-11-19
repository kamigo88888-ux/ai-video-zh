@echo off
chcp 65001 >nul
cd /d "%~dp0"
title AI 電商影片自動中文化 - 安裝
echo ==================================================
echo   AI 電商影片自動中文化  一鍵安裝（只需執行一次）
echo ==================================================
echo.

rem ---- 1. Python ----
set "PY="
py -3.12 -c "import sys" >nul 2>nul && set "PY=py -3.12"
if not defined PY python -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul && set "PY=python"
if not defined PY (
  echo [1/3] 找不到 Python，正在自動安裝 Python 3.12 ...
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
  echo.
  echo Python 安裝完成。請「關閉這個視窗」，再重新雙擊 install_windows.bat 繼續安裝。
  pause
  exit /b
)
echo [1/3] Python OK

rem ---- 2. ffmpeg ----
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo [2/3] 找不到 ffmpeg，正在自動安裝 ...
  winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
  echo.
  echo ffmpeg 安裝完成。請「關閉這個視窗」，再重新雙擊 install_windows.bat 繼續安裝。
  pause
  exit /b
)
echo [2/3] ffmpeg OK

rem ---- 3. Python packages (in a private .venv folder) ----
echo [3/3] 安裝 Python 套件（第一次約 3-5 分鐘）...
if not exist ".venv\Scripts\python.exe" %PY% -m venv .venv
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo 套件安裝失敗，請把上面的錯誤訊息截圖給 Claude。
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -c "import rapidocr_onnxruntime, cv2, fastapi, anthropic; print('套件檢查 OK')"

echo.
echo ==================================================
echo   安裝完成！之後每次使用請雙擊  start_windows.bat
echo ==================================================
pause
