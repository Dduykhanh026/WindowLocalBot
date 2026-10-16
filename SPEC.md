# SPEC — Bot Local Web (Windows)

## 1. Mục tiêu

Bot chat chạy **local 100%** trên máy Windows, giao diện web mở trong trình duyệt.
Người dùng hỏi nhanh: thời tiết, giờ giấc, ngày tháng. Không cần server ngoài,
không cần tài khoản, không cần Telegram/Discord.

Bản này thay thế bản Tkinter cũ vì Tkinter xấu và khó tùy biến giao diện.
Logic trả lời giữ nguyên, chỉ thay lớp giao diện sang web.

## 2. Đối tượng & ngữ cảnh sử dụng

- 1 người dùng duy nhất (Nguyen) trên 1 máy Windows.
- Mở bot vài lần/ngày, hỏi 1–2 câu rồi đóng. Cần mở nhanh, gõ nhanh, đọc nhanh.
- Máy có thể không có mạng lúc hỏi giờ/ngày → giờ/ngày vẫn phải chạy.
- Thời tiết cần mạng (Open-Meteo). Mất mạng phải báo rõ, không treo UI.

## 3. Tính năng V1 (bắt buộc — đủ là xong, không thêm)

| # | Input (vd) | Output mong đợi |
|---|------------|-----------------|
| 1 | `thời tiết` | Thời tiết hiện tại tại Sài Gòn (mặc định) |
| 2 | `thời tiết Hà Nội`, `thoi tiet Da Nang`, `thời tiết Đà Lạt` | Thời tiết hiện tại tại thành phố đó. Gõ có/không dấu đều được. Thành phố không tồn tại → báo “Không tìm thấy địa điểm …” |
| 3 | `thời tiết ngày mai Đà Lạt` | Dự báo ngày mai: mô tả + min/max + % mưa |
| 4 | `mấy giờ`, `giờ` | Giờ hiện tại (giờ Việt Nam) + thứ + ngày |
| 5 | `giờ ở Tokyo` | Giờ tại Tokyo / Seoul / London / Paris / New York / Sydney / Bangkok / Singapore… (danh sách đã có trong `bot.py`) |
| 6 | `hôm nay`, `ngày mấy` | Thứ + ngày tháng hôm nay |
| 7 | `help` | Liệt kê tất cả lệnh |
| 8 | `hello`, `cảm ơn`, `tạm biệt` | Chào / cảm ơn / tạm biệt thân thiện |
| 9 | Câu linh tinh | Fallback: “Mình chưa hiểu…” + gợi ý 3 lệnh mẫu |

- Thời tiết hiển thị: mô tả (vd “Mưa nhẹ 🌧️”), nhiệt độ + cảm giác như, độ ẩm, gió.
- Giờ Việt Nam dùng múi `Asia/Ho_Chi_Minh`.
- Không làm: nhắc việc, ghi chú, lịch sử chat lưu DB, đăng nhập, AI/LLM, thông báo, đa người dùng.

## 4. Giao diện (web)

### 4.1 Layout

- Cửa sổ chat 1 cột, rộng tối đa ~720px, căn giữa, full chiều cao trình duyệt.
- Header: tên “🤖 Bot Local” + dòng nhỏ “Chạy local trên máy này”.
- Khung chat: cuộn dọc, tin nhắn mới tự cuộn xuống cuối.
- Tin nhắn user: bong bóng phải, nền xanh nhạt. Tin nhắn bot: bong bóng trái, nền xám nhạt.
- Hàng nút nhanh (4 nút): `🌤️ Thời tiết Sài Gòn` | `🕐 Mấy giờ` | `📅 Hôm nay` | `❓ Help` — bấm là gửi luôn, không cần gõ.
- Ô nhập + nút Gửi. Enter = gửi. Sau khi gửi, ô nhập trống + focus lại.
- Khi đang chờ bot (đang lấy thời tiết): hiện “Bot đang trả lời…” / spinner, disable nút Gửi tạm thời, không cho gửi trùng.
- Hỗ trợ xuống dòng trong câu trả lời bot (bot trả về nhiều dòng, hiển thị giữ nguyên xuống dòng).

### 4.2 Thẩm mỹ (bắt buộc — đây là lý do làm bản web)

- Bo góc bong bóng + nút + ô nhập (radius 12–18px), có padding thoáng, không để giao diện vuông thô như Tkinter.
- Font `Segoe UI`, cỡ chữ chat 15–16px, line-height 1.5.
- Light mode là đủ cho V1. Dark mode là nice-to-have, chỉ làm nếu còn thời gian và không phá deadline — không bắt buộc.
- Responsive: ở cửa sổ hẹp (400px) vẫn dùng được, nút nhanh xuống dòng gọn.
- Không dùng emoji quá lố ngoài emoji bot đã trả về.

### 4.3 Hành vi JS

- Gửi tin nhắn → `POST /api/chat` (JSON) → nhận `{reply}` → append vào khung chat.
- Lỗi mạng / server lỗi → hiển thị bong bóng bot: “⚠️ Có lỗi gì đó, thử lại nhé.”
- Không reload trang khi gửi. Không dùng form submit truyền thống.
- Không lưu lịch sử chat vào localStorage ở V1 (mở lại trang là chat mới, kèm câu chào).

## 5. Backend & chạy

### 5.1 API

- `GET /` → trả `templates/index.html`.
- `POST /api/chat` — Request: `{"message": "..."}`. Response: `{"reply": "..."}`.
  - `message` rỗng → reply: `"Gõ gì đó đi, vd: \`thời tiết\`, \`mấy giờ\` 😄"` (dùng đúng logic `bot.py`).
  - Lỗi bất ngờ trong `get_reply()` → HTTP 200 + `{"reply": "⚠️ Có lỗi gì đó, thử lại nhé."}` (không trả 500 ra UI).
- `GET /api/health` → `{"status": "ok"}` (để kiểm tra server sống).
- Gọi `get_reply()` trực tiếp, không qua queue, không qua thread phức tạp. Flask dev server là đủ.

### 5.2 Cấu trúc file cuối cùng

```
windows-local-bot-web/
  AGENTS.md / SPEC.md / TASKS.md / REPORT.md   (scaffold, không ship cho user cuối)
  app.py                # Flask app: routes /, /api/chat, /api/health
  bot.py                # ĐÃ CÓ — giữ nguyên
  weather.py            # ĐÃ CÓ — giữ nguyên
  requirements.txt      # Chỉ 1 dòng: Flask==<version chốt lúc code, vd 3.x>
  run.bat               # Entry point Windows (xem 5.3)
  README.md             # Hướng dẫn chạy cho người không biết code
  templates/
    index.html          # Khung chat, không nhúng CSS/JS dài vào đây
  static/
    style.css           # Toàn bộ CSS
    app.js              # Toàn bộ JS
```

### 5.3 `run.bat` (quan trọng — phải test trên Windows)

- `chcp 65001` để hiển thị tiếng Việt đúng.
- Kiểm tra `python` có trong PATH, nếu chưa có → in hướng dẫn tải Python + tick “Add python.exe to PATH” rồi `pause`.
- Nếu chưa có Flask → `pip install -r requirements.txt` (chỉ chạy khi cần, kiểm tra bằng `python -c "import flask"` trước).
- Chạy `python app.py`, sau đó tự mở trình duyệt `http://127.0.0.1:5000`.
- Quyết định console: dùng `python` (giữ cửa sổ console để thấy lỗi) — đơn giản, ưu tiên cho V1. Không dùng `pythonw` ở V1 để tránh giấu lỗi.
- Khi server chạy, `run.bat` phải in rõ: `Bot đang chạy tại http://127.0.0.1:5000 — đừng tắt cửa sổ này.`

### 5.4 Port

- Mặc định port `5000`, host `127.0.0.1`.
- Nếu port 5000 bận → in lỗi rõ ràng “Port 5000 đang bận, tắt app khác hoặc đổi port trong app.py”, không tự nhảy port lung tung.

## 6. Phi chức năng

- Khởi động: từ lúc double-click `run.bat` đến lúc chat được ≤ 10 giây (sau khi đã cài Python + Flask).
- Thời tiết: timeout API ≤ 10s (đã có trong `weather.py`), không để UI treo quá 10s.
- Bảo mật: chỉ listen localhost, không expose ra LAN/internet. Không log tin nhắn user ra file.
- Tương thích: Windows 10/11, Python 3.11+, Chrome/Edge mới.

## 7. Không làm ở V1 (ghi rõ để agent không tự thêm)

- Dark mode (nice-to-have, không bắt buộc), lưu lịch sử chat, đồng bộ, đa tab.
- Voice input, TTS, AI/LLM (Gemini/OpenAI), nhắc việc, ghi chú.
- Đóng gói `.exe` (PyInstaller), auto-start cùng Windows, tray icon.
- PWA manifest / service worker — chỉ làm nếu còn thời gian sau khi V1 pass hết, không bắt buộc.

## 8. Tiêu chí nghiệm thu V1

- `run.bat` chạy được trên Windows, trình duyệt tự mở, chat được ngay.
- 9 dòng tính năng ở §3 đều pass theo checklist TASKS Phase 4.
- Tắt mạng → hỏi giờ/ngày vẫn được, hỏi thời tiết báo mất mạng rõ ràng.
- Giao diện không còn “vuông thô” kiểu Tkinter: có bo góc, bong bóng chat, nút nhanh hoạt động.
