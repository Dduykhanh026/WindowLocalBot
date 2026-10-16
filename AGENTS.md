# AGENTS.md — Bot Local Web (Windows)

> File này là hướng dẫn cho agent code (agy). Đọc kỹ trước khi code.
> Nguồn duy nhất của sự thật: `SPEC.md`. Thứ tự làm việc: `TASKS.md`.

## 1. Bối cảnh

- Project: Bot chat local chạy trên máy Windows, giao diện **web** mở trong trình duyệt.
- Đây là bản web thay cho bản Tkinter cũ (đã có sẵn logic, xem mục 2).
- Người dùng: Nguyen, dev iOS/SwiftUI + Flutter, dùng máy Windows. Bot để hỏi nhanh thời tiết / giờ giấc.
- Ngôn ngữ giao diện + trả lời: **tiếng Việt**, hỗ trợ gõ có dấu và không dấu.

## 2. Điểm bắt đầu — KHÔNG viết lại logic

- `bot.py` và `weather.py` trong thư mục này **đã đúng và đã test** (copy từ bản Tkinter).
  - `bot.py`: `get_reply(message) -> str` — hiểu câu hỏi, trả lời giờ/ngày/thời tiết/help.
  - `weather.py`: `get_current_weather(city)`, `get_tomorrow_weather(city)` — gọi Open-Meteo, đã fix lỗi chữ `Đ` (Đà Lạt/Đà Nẵng) và lỗi `Hà Nội, Hanoi`.
- **Việc của agent là xây lớp web xung quanh 2 file này, KHÔNG viết lại logic trong chúng** trừ khi phát hiện bug thật và ghi rõ trong `REPORT.md`.
- Nếu cần sửa `bot.py` / `weather.py`, chỉ sửa tối thiểu và giữ nguyên chữ ký hàm + format trả lời.

## 3. Stack chốt (không tự đổi)

- Backend: **Python 3.11+ + Flask** (duy nhất 1 dependency ngoài stdlib).
- Frontend: **HTML + CSS + JS thuần**, không npm, không build, không CDN, không Tailwind, không framework JS. CSS/JS để trong `static/`, HTML trong `templates/`.
- Chạy trên Windows: `run.bat` là entry point chính.
- Không dùng: Electron, PyQt, Tkinter, CustomTkinter, FastAPI, Django, Node, database, login, AI/LLM.

## 4. Quy tắc code

- Code đơn giản, đọc được ngay, không over-engineering. Một file làm một việc.
- Tên file / hàm / biến tiếng Anh, comment tiếng Việt ngắn gọn khi cần.
- Giao diện tiếng Việt, font `Segoe UI`, hỗ trợ gõ không dấu.
- Mọi lỗi phải trả về câu tiếng Việt thân thiện, không để traceback lộ ra UI.
- Server chỉ bind `127.0.0.1` (localhost), **không** bind `0.0.0.0`.
- Không gọi API nào khác ngoài Open-Meteo (đã có trong `weather.py`). Không thêm API key, không thêm tracking.
- Không hardcode port khác ngoài `5000` trừ khi port bận (xem SPEC §5).
- Không tự thêm tính năng ngoài SPEC V1. Ý tưởng mới ghi vào `REPORT.md` mục "Backlog", không code luôn.

## 5. Quy trình làm việc

1. Làm theo đúng thứ tự Phase trong `TASKS.md`. Không nhảy Phase.
2. Mỗi Phase xong phải tự chạy lệnh verify ghi trong `TASKS.md` và ghi kết quả thật vào `REPORT.md`.
3. Khi gặp lỗi, sửa code, chạy lại verify, không đoán mò.
4. Cuối mỗi Phase: commit rõ ràng nếu đang ở trong git worktree (1 commit / Phase, message tiếng Việt ngắn gọn).
5. Cuối cùng: cập nhật `REPORT.md` đầy đủ + `README.md` hướng dẫn chạy.

## 6. Definition of Done (toàn project)

- [ ] `run.bat` trên Windows: double-click → server chạy → trình duyệt tự mở `http://127.0.0.1:5000` → chat được ngay.
- [ ] Mọi lệnh trong SPEC §3 đều trả lời đúng (test theo checklist TASKS Phase 4).
- [ ] Không còn cửa sổ console đen thừa ngoài ý muốn — `run.bat` dùng `python` (giữ console để thấy lỗi) hoặc `pythonw` theo quyết định ghi trong SPEC §5, không để cả 2 lẫn lộn.
- [ ] `README.md` người không biết code đọc là chạy được.
- [ ] `REPORT.md` ghi đủ: đã làm gì, test gì, kết quả, lỗi còn tồn tại (nếu có).
