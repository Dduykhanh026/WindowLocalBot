@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"

python --version >nul 2>&1
if errorlevel 1 goto :no_python

python -c "import flask, edge_tts" >nul 2>&1
if errorlevel 1 goto :install_flask

:start_bot
set "OLLAMA_CMD="
where ollama >nul 2>&1
if not errorlevel 1 (
    set "OLLAMA_CMD=ollama"
) else if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" (
    set "OLLAMA_CMD=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
)

if defined OLLAMA_CMD (
    netstat -ano | findstr /R /C:":11434 .*LISTENING" >nul 2>&1
    if errorlevel 1 (
        echo Đang khởi động bộ não AI Local ^(Ollama^)...
        start "" /b "%OLLAMA_CMD%" serve >nul 2>&1
    )
)

echo Bot đang chạy tại http://127.0.0.1:5000 — đừng tắt cửa sổ này.
start "" /b cmd /c "ping 127.0.0.1 -n 3 >nul & start http://127.0.0.1:5000"
python app.py
echo.
pause
exit /b 0

:install_flask
echo Đang cài đặt Flask từ requirements.txt, vui lòng đợi...
pip install -r requirements.txt
if errorlevel 1 goto :pip_error
echo.
goto :start_bot

:no_python
echo [LỖI] Không tìm thấy Python trong PATH!
echo 1. Tải Python 3.11+ tại: https://www.python.org/downloads/
echo 2. Khi cài đặt, nhớ tick ô "Add python.exe to PATH".
echo 3. Sau khi cài xong, mở lại file run.bat này.
echo.
pause
exit /b 1

:pip_error
echo.
echo [LỖI] Không cài được Flask. Kiểm tra lại kết nối mạng rồi thử lại nhé.
pause
exit /b 1