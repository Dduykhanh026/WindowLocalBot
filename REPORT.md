# REPORT — Bot Local Web (Windows)

> Agent code cập nhật file này trong lúc làm. Template sẵn để điền.

## Trạng thái

- [ ] Chưa bắt đầu
- [ ] Đang làm (Phase 4 / 4)
- [x] Đã xong, chờ Nguyen test trên Windows
- [ ] Đã nghiệm thu

## Đã làm

- **Phase 0**:
  - Đọc đầy đủ `AGENTS.md`, `SPEC.md`, `TASKS.md`.
  - Kiểm tra môi trường: `Python 3.11.8`, `Flask 3.1.3`.
  - Chạy thử `get_reply('mấy giờ')` và `get_reply('thời tiết Hà Nội')` từ `bot.py` → trả về đúng giờ Việt Nam và thời tiết Hà Nội từ Open-Meteo.
- **Phase 1** (nhánh worktree `feat/phase1-backend`, commit `fda0a0e`):
  - Tạo `requirements.txt` (`Flask==3.1.3`).
  - Tạo `app.py` với 3 route: `GET /` (render `templates/index.html` kèm `greeting=GREETING`), `POST /api/chat` (gọi `get_reply()`, bọc `try/except` trả HTTP 200 kèm fallback thân thiện nếu có lỗi bất ngờ), và `GET /api/health` (`{"status": "ok"}`).
  - Cấu hình `app.run(host="127.0.0.1", port=5000, debug=False)` và bắt `OSError` khi port 5000 bận để in rõ: `"Port 5000 đang bận, tắt app khác hoặc đổi port trong app.py"`.
- **Phase 2** (nhánh worktree `feat/phase2-frontend`, commit `f34daa1`):
  - Tạo `templates/index.html`: khung chat 1 cột, header `🤖 Bot Local` + `Chạy local trên máy này`, bong bóng chào lấy nguyên bản `GREETING` từ `bot.py`, indicator `"Bot đang trả lời…"`, 4 nút nhanh (`🌤️ Thời tiết Sài Gòn`, `🕐 Mấy giờ`, `📅 Hôm nay`, `❓ Help`), ô nhập `#msg` và nút `#sendBtn`.
  - Tạo `static/style.css`: giao diện sáng hiện đại, font `Segoe UI`, cỡ chữ chat `15.5px`, `line-height: 1.5`, bong bóng user (phải, xanh nhạt `#dbeafe`) và bot (trái, xám nhạt `#f1f5f9`) bo góc `16px`, giữ xuống dòng bằng `white-space: pre-wrap`, responsive gọn ở màn hình hẹp `400px`.
  - Tạo `static/app.js`: gửi tin nhắn qua `fetch POST /api/chat`, khóa nút Gửi/input/nút nhanh khi đang chờ để tránh gửi trùng, chống XSS tuyệt đối bằng `textContent`, hỗ trợ phím `Enter`, tự cuộn xuống cuối và `focus()` lại ô nhập.
- **Phase 3** (nhánh worktree `feat/phase3-packaging`, commit `faed1c8`):
  - Tạo `run.bat`: bật UTF-8 (`chcp 65001`, `PYTHONUTF8=1`), kiểm tra Python trong PATH, kiểm tra & tự cài Flask nếu thiếu, in thông báo `Bot đang chạy tại http://127.0.0.1:5000 — đừng tắt cửa sổ này.`, tự mở trình duyệt sau 2 giây và chạy `python app.py`.
  - Tạo `README.md`: hướng dẫn 2 bước cho người không biết code, bảng lệnh đầy đủ, mẹo ghim shortcut `run.bat` lên Taskbar Windows và cách xử lý 3 lỗi thường gặp.
  - Dọn dẹp toàn bộ worktree tạm và `__pycache__`.
- **Phase 4**:
  - Chạy kiểm thử toàn bộ 15 mục trong QA checklist trên server thật `http://127.0.0.1:5000`.

## Kết quả test

- `GET /api/health`: HTTP 200 — `{"status":"ok"}`
- Checklist Phase 4: **15 / 15 pass**
  - **4.1** `thời tiết` → `🌤️ Thời tiết hiện tại tại Thành phố Hồ Chí Minh: • Mưa phùn dày 🌦️ • Nhiệt độ: 29.1°C (cảm giác như 34.3°C) • Độ ẩm: 79% • Gió: 10.6 km/h` (PASS)
  - **4.2** `thời tiết Hà Nội` → `🌤️ Thời tiết hiện tại tại Hà Nội: • Trời quang đãng ☀️ • Nhiệt độ: 31.2°C (cảm giác như 34.2°C) • Độ ẩm: 57% • Gió: 12.9 km/h` — không bị lặp "Hà Nội, Hanoi" (PASS)
  - **4.3** `thoi tiet Da Nang` → `🌤️ Thời tiết hiện tại tại Đà Nẵng: • Trời quang, có chút mây ☀️ • Nhiệt độ: 28.8°C (cảm giác như 32.9°C) • Độ ẩm: 72% • Gió: 10.9 km/h` (PASS)
  - **4.4** `thời tiết Đà Lạt` → `🌤️ Thời tiết hiện tại tại Đà Lạt, Lam Dong: • Mưa rào vừa 🌦️ • Nhiệt độ: 18.8°C (cảm giác như 20.5°C) • Độ ẩm: 90% • Gió: 5.6 km/h` (PASS)
  - **4.5** `thời tiết ngày mai Đà Lạt` → `🌤️ Dự báo ngày mai tại Đà Lạt, Lam Dong: • Giông bão ⛈️ • Nhiệt độ: 16.4°C – 24.6°C • Khả năng mưa: 100%` (PASS)
  - **4.6** `thời tiết xyzabc123` → `⚠️ Không tìm thấy địa điểm “xyzabc123”. Thử gõ tên thành phố khác, vd: Hà Nội, Đà Nẵng, Đà Lạt.` (PASS)
  - **4.7** `mấy giờ` → `🕐 Bây giờ là 15:00:52 — Thứ Sáu, 16/10/2026 (giờ Việt Nam).` (PASS)
  - **4.8** `giờ ở Tokyo` → `🕐 Bây giờ ở Tokyo là 17:00 — Thứ Sáu, 16/10/2026.` (PASS)
  - **4.9** `hôm nay` → `📅 Hôm nay là Thứ Sáu, ngày 16/10/2026.` (PASS)
  - **4.10** `help` → Hiển thị đầy đủ 7 dòng lệnh hướng dẫn (PASS)
  - **4.11** Câu linh tinh (`blablabla 123`) → `Hmm, mình chưa hiểu câu đó 😅 Thử: thời tiết Hà Nội, mấy giờ, hôm nay — hoặc gõ help để xem tất cả lệnh.` (PASS)
  - **4.12** Mất mạng (giả lập ngắt kết nối `URLError`) → `mấy giờ` vẫn trả giờ VN bình thường; `thời tiết` trả `⚠️ Không có mạng rồi — kiểm tra lại Wi-Fi rồi thử lại nhé.` (PASS)
  - **4.13** Giao diện: bo góc 14–16px, bong bóng chat trái/phải, 4 nút nhanh hoạt động, responsive ở 400px (PASS)
  - **4.14** `GET /api/health` → `{"status":"ok"}` (PASS)
  - **4.15** Không còn file thừa hay `__pycache__`, cấu trúc thư mục khớp 100% SPEC §5.2 (PASS)
- Test trên môi trường: **Windows + Python 3.11.8 + Flask 3.1.3**.

## Thay đổi so với SPEC (nếu có)

- Không đổi (`bot.py` và `weather.py` giữ nguyên 100%).

## Lỗi còn tồn tại / lưu ý

- Không có lỗi tồn tại. Lưu ý nhỏ: trên Windows, cần giữ cửa sổ console `run.bat` trong lúc sử dụng (đã in thông báo rõ trên console và trong `README.md`).

## Backlog (ý tưởng V2, KHÔNG code ở V1)

- Dark mode tự động theo hệ điều hành.
- Lưu lịch sử chat vào `localStorage` kèm nút "Xóa đoạn chat".
- Đóng gói `.exe` độc lập bằng PyInstaller hoặc chạy ngầm dưới System Tray (`pystray`).
- Tự khởi động cùng Windows, hỗ trợ PWA manifest, nhắc việc cá nhân.
