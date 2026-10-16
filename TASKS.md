# TASKS — Bot Local Web (Windows)

> Làm đúng thứ tự. Mỗi Phase xong phải chạy lệnh Verify và ghi kết quả vào REPORT.md.
> Không nhảy Phase. Không thêm tính năng ngoài SPEC V1.

## Phase 0 — Chuẩn bị (5 phút)

- [x] 0.1 Đọc `AGENTS.md` + `SPEC.md` hết một lượt.
- [x] 0.2 Kiểm tra `bot.py` + `weather.py` đã có trong thư mục, chạy thử:
  ```bash
  python3 -c "from bot import get_reply; print(get_reply('mấy giờ')); print(get_reply('thời tiết Hà Nội'))"
  ```
  Verify: in ra giờ hiện tại + thời tiết Hà Nội, không lỗi.
- [x] 0.3 Kiểm tra Python version: `python --version` (yêu cầu 3.11+).

## Phase 1 — Backend Flask (xương sống)

- [x] 1.1 Tạo `app.py`:
  - Import Flask + `get_reply` từ `bot.py`.
  - `GET /` → render `templates/index.html`.
  - `POST /api/chat` → đọc JSON `{"message": str}`, gọi `get_reply(message)`, trả `{"reply": str}`. Bọc `try/except` → lỗi vẫn trả 200 + reply fallback `"⚠️ Có lỗi gì đó, thử lại nhé."`.
  - `GET /api/health` → `{"status": "ok"}`.
  - `if __name__ == "__main__":` chạy `app.run(host="127.0.0.1", port=5000, debug=False)`.
  - Không bật `debug=True` ở bản giao.
- [x] 1.2 Tạo `requirements.txt` — chỉ 1 dòng `Flask==<version đã test, vd 3.1.x>`. Không thêm gì khác.
- [x] 1.3 Tạo `templates/index.html` placeholder tối thiểu (h1 “Bot Local”, div#chat, input, button) để Phase 1 test được API.
- [x] 1.4 Tạo `static/style.css` + `static/app.js` placeholder rỗng (sẽ làm đầy ở Phase 2).
- Verify Phase 1:
  ```bash
  pip install -r requirements.txt
  python app.py
  # Tab khác / terminal khác:
  curl http://127.0.0.1:5000/api/health
  curl -X POST http://127.0.0.1:5000/api/chat -H "Content-Type: application/json" -d "{\"message\":\"mấy giờ\"}"
  curl -X POST http://127.0.0.1:5000/api/chat -H "Content-Type: application/json" -d "{\"message\":\"thời tiết Hà Nội\"}"
  curl -X POST http://127.0.0.1:5000/api/chat -H "Content-Type: application/json" -d "{\"message\":\"\"}"
  ```
  Pass khi: `/api/health` → `{"status":"ok"}`, 3 câu chat đều trả `reply` tiếng Việt đúng, câu rỗng không crash.

## Phase 2 — Giao diện web (phần quan trọng nhất)

- [x] 2.1 `templates/index.html` đầy đủ theo SPEC §4.1:
  - Header, div#chat (khung chat cuộn), hàng 4 nút nhanh, ô input#msg + nút Gửi#sendBtn.
  - Câu chào bot khi mở trang (lấy đúng text `GREETING` trong `bot.py`, không tự bịa câu chào khác).
  - Link `style.css` + `app.js` bằng `url_for('static', ...)`, không nhúng CSS/JS dài vào HTML.
  - `lang="vi"`, `<meta charset="utf-8">`, `<meta name="viewport">`.
- [x] 2.2 `static/style.css` đầy đủ theo SPEC §4.2:
  - Layout 1 cột, max-width ~720px, căn giữa, full height.
  - Bong bóng user (phải, xanh nhạt) / bot (trái, xám nhạt), bo góc 12–18px, padding thoáng.
  - Nút nhanh + ô nhập + nút Gửi bo góc, font `Segoe UI`, chat 15–16px, line-height 1.5.
  - Trạng thái “Bot đang trả lời…” nhìn rõ.
  - Responsive ở 400px: nút nhanh xuống dòng gọn, không vỡ layout.
- [x] 2.3 `static/app.js` đầy đủ theo SPEC §4.3:
  - `sendMessage(text)`: lấy text từ input hoặc nút nhanh, append bong bóng user, hiện indicator, `fetch POST /api/chat`, append bong bóng bot (giữ xuống dòng — dùng `white-space: pre-wrap`), ẩn indicator, focus lại input.
  - Enter = gửi. Disable nút Gửi + input trong lúc chờ (tránh gửi trùng).
  - Lỗi fetch / server lỗi → append bong bóng bot `"⚠️ Có lỗi gì đó, thử lại nhé."`.
  - Không dùng form submit reload trang. Không dùng localStorage ở V1.
  - Escape HTML trong reply trước khi hiển thị (tránh XSS từ tên thành phố user gõ).
- Verify Phase 2 (bằng tay trên trình duyệt):
  - Mở `http://127.0.0.1:5000`, thấy header + 4 nút nhanh + ô nhập.
  - Gửi `mấy giờ` → thấy bong bóng user phải + bong bóng bot trái, đúng giờ.
  - Bấm 4 nút nhanh → mỗi nút gửi được ngay, không cần gõ.
  - Gửi `thời tiết Hà Nội` → thấy indicator rồi mới hiện kết quả, UI không đơ.
  - Thu hẹp cửa sổ còn 400px → layout không vỡ.

## Phase 3 — `run.bat` + README + dọn dẹp

- [x] 3.1 Tạo `run.bat` theo SPEC §5.3:
  - `chcp 65001`, `cd /d "%~dp0"`, kiểm tra `python`, kiểm tra Flask (`python -c "import flask"`), nếu thiếu thì `pip install -r requirements.txt`.
  - In dòng `Bot đang chạy tại http://127.0.0.1:5000 — đừng tắt cửa sổ này.`
  - Mở trình duyệt: `start http://127.0.0.1:5000` (đợi server lên ~2s trước khi mở, dùng `timeout /t 2`).
  - Chạy `python app.py`. Lỗi thì `pause` để user đọc lỗi.
- [x] 3.2 Tạo `README.md` cho người không biết code:
  - Cách chạy: 1) Cài Python 3.11+ (tick “Add python.exe to PATH”), 2) Double-click `run.bat`.
  - Bảng lệnh: `thời tiết`, `thời tiết Hà Nội`, `thời tiết ngày mai Đà Lạt`, `mấy giờ`, `giờ ở Tokyo`, `hôm nay`, `help`.
  - Cách ghim lên taskbar: tạo shortcut từ `run.bat` → Pin to taskbar.
  - Cách sửa lỗi thường gặp: chưa cài Python, port 5000 bận, mất mạng khi hỏi thời tiết.
- [x] 3.3 Dọn dẹp: xóa placeholder / file thừa, kiểm tra cấu trúc đúng SPEC §5.2. Không để file `__pycache__` / `.pyc` trong bản giao.
- Verify Phase 3:
  - Trên Windows: double-click `run.bat` → trình duyệt tự mở → chat được.
  - `README.md` đọc từ đầu đến cuối, làm theo là chạy được (không thiếu bước).

## Phase 4 — QA cuối (checklist bắt buộc, ghi kết quả vào REPORT.md)

Chạy từng dòng, tick pass/fail:

- [x] 4.1 `thời tiết` → thời tiết Sài Gòn, có nhiệt độ + độ ẩm + gió.
- [x] 4.2 `thời tiết Hà Nội` → đúng Hà Nội (không còn “Hà Nội, Hanoi”).
- [x] 4.3 `thoi tiet Da Nang` (không dấu) → đúng Đà Nẵng.
- [x] 4.4 `thời tiết Đà Lạt` (chữ Đ) → đúng Đà Lạt, không ra địa điểm lạ.
- [x] 4.5 `thời tiết ngày mai Đà Lạt` → dự báo ngày mai, có min/max + % mưa.
- [x] 4.6 `thời tiết xyzabc123` → báo không tìm thấy địa điểm, không crash.
- [x] 4.7 `mấy giờ` → đúng giờ VN + thứ + ngày.
- [x] 4.8 `giờ ở Tokyo` → đúng giờ Tokyo.
- [x] 4.9 `hôm nay` → đúng thứ + ngày.
- [x] 4.10 `help` → liệt kê đủ lệnh.
- [x] 4.11 Câu linh tinh → fallback + gợi ý lệnh.
- [x] 4.12 Mất mạng (tắt Wi-Fi) → `mấy giờ` vẫn được, `thời tiết` báo mất mạng rõ ràng.
- [x] 4.13 Giao diện: bo góc + bong bóng chat + 4 nút nhanh hoạt động + responsive 400px.
- [x] 4.14 `GET /api/health` → `{"status":"ok"}`.
- [x] 4.15 Không còn file thừa / lỗi console khi chạy `run.bat`.

## Hoàn tất

- [x] Cập nhật `REPORT.md` đầy đủ (xem template).
- [x] Nếu đang ở git worktree: commit theo Phase, message rõ ràng.
