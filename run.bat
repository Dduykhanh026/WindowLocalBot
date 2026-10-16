@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"

:: Kiểm tra Python đã được cài đặt và thêm vào PATH chưa
python --version >nul 2>&1
if errorlevel 1 (
    echo [LỖI] Không tìm thấy Python trên máy của bạn!
    echo.
    echo Hướng dẫn cài đặt:
    echo   1. Tải Python 3.11+ tại: https://www.python.org/downloads/
    echo   2. Khi chạy bộ cài đặt, BẮT BUỘC phải tick vào ô "Add python.exe to PATH" ở dưới cùng cửa sổ.
    echo   3. Cài xong, hãy đóng cửa sổ này và mở lại run.bat.
    echo.
    pause
    exit /b 1
)

:: Kiểm tra thư viện Flask đã được cài đặt chưa
python -c "import flask" >nul 2>&1
if errorlevel 1 (
    echo Đang cài đặt thư viện cần thiết (Flask)... Vui lòng đợi...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo [LỖI] Không thể cài đặt Flask. Vui lòng kiểm tra kết nối mạng và thử lại.
        pause
        exit /b 1
    )
    echo Cài đặt hoàn tất!
    echo.
)

echo Bot đang chạy tại http://127.0.0.1:5000 — đừng tắt cửa sổ này.
echo Trình duyệt sẽ tự động mở sau 2 giây...
echo.

:: Mở trình duyệt sau 2 giây để đợi server Flask khởi động xong
start "" /b cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:5000"

:: Chạy server Flask
python app.py
if errorlevel 1 (
    echo.
    echo [LỖI] Server đã dừng do có lỗi xảy ra.
    pause
)
