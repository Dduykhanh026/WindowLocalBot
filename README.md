# 🤖 Wisp — Bot AI Local Web (Windows)

Bot chat cá nhân thông minh chạy **100% trên máy tính Windows** của bạn với giao diện web gọn đẹp, mở ngay trong trình duyệt. Wisp kết hợp **Fast-path tra cứu tức thì** (dữ liệu thời tiết thực tế từ **Open-Meteo**, giờ quốc tế, ngày tháng) cùng bộ não **AI Local Qwen 2.5 (3B)** chạy qua **Ollama** ngay trên máy — không cần tài khoản, không tốn phí API, bảo mật tuyệt đối.

---

## 🚀 Cách chạy nhanh (2 bước)

### Bước 1: Cài đặt Python (chỉ cần làm 1 lần đầu tiên)
1. Tải **Python 3.11 trở lên** tại trang chủ: [https://www.python.org/downloads/](https://www.python.org/downloads/)
2. Mở file vừa tải để cài đặt.
3. ⚠️ **QUAN TRỌNG:** Ở màn hình cài đặt đầu tiên, **bắt buộc phải tick vào ô `"Add python.exe to PATH"`** ở dưới cùng trước khi bấm **Install Now**.

### Bước 2: Mở Bot
- Nhấp đúp (**double-click**) vào file **`run.bat`** trong thư mục này.
- `run.bat` sẽ tự động kiểm tra thư viện cần thiết, khởi động ngầm **Ollama** (`127.0.0.1:11434`) nếu có trên máy, và bật Web Server của Wisp.
- Sau đó trình duyệt sẽ **tự động mở** trang chat tại địa chỉ `http://127.0.0.1:5000`.
- *(Lưu ý: Giữ nguyên cửa sổ dòng lệnh màu đen trong lúc chat. Khi nào dùng xong bạn chỉ cần tắt cửa sổ đó đi).*

---

## 💬 Các câu lệnh & Tính năng AI Local

Bạn có thể bấm trực tiếp vào **4 nút gợi ý nhanh** trên giao diện hoặc gõ câu hỏi (hỗ trợ **cả tiếng Việt có dấu và không dấu**):

| Bạn muốn hỏi | Câu lệnh mẫu | Kết quả Wisp trả về |
|---|---|---|
| **Thời tiết mặc định** | `thời tiết` | Thời tiết hiện tại ở Sài Gòn (nhiệt độ, cảm giác thực, độ ẩm, tốc độ gió) |
| **Thời tiết các tỉnh/thành** | `thời tiết Hà Nội`<br>`thoi tiet Da Nang`<br>`thời tiết Đà Lạt` | Thời tiết hiện tại tại địa điểm bạn muốn tra cứu (hỗ trợ cả gõ không dấu) |
| **Dự báo thời tiết ngày mai** | `thời tiết ngày mai Đà Lạt`<br>`thời tiết ngày mai` | Dự báo ngày mai gồm mô tả thời tiết, nhiệt độ thấp nhất/cao nhất và khả năng mưa (%) |
| **Giờ Việt Nam hiện tại** | `mấy giờ`, `giờ` | Giờ hiện tại ở Việt Nam kèm thứ và ngày tháng |
| **Giờ quốc tế** | `giờ ở Tokyo`, `giờ ở London`,<br>`giờ ở New York`, `giờ ở Seoul`,<br>`giờ ở Sydney`, `giờ ở Bangkok`,<br>`giờ ở Singapore`, `giờ ở Paris` | Giờ hiện tại ở các thành phố lớn trên thế giới (hỗ trợ thêm cả Mỹ, Anh, Nhật, Hàn, Pháp, Đức, Úc, Thái Lan...) |
| **Ngày tháng hôm nay** | `hôm nay`, `ngày mấy` | Thứ trong tuần và ngày/tháng/năm hiện tại |
| **Tư vấn thời tiết bằng AI** | `thời tiết Sài Gòn hôm nay mặc gì hợp?`<br>`ngày mai Đà Lạt có mưa không, nên đi đâu?` | Tự động lấy số liệu thật từ **Open-Meteo** kết hợp **AI Qwen 2.5 (3B)** để tư vấn trang phục, lịch trình |
| **Hỏi đáp, Trò chuyện & Code (AI Local)** | `viết hàm SwiftUI tạo nút bo góc`<br>`giải thích StateWidget trong Flutter`<br>`kể chuyện vui` | Trả lời thông minh bằng tiếng Việt qua model **Qwen 2.5 (3B)** chạy trên **Ollama**, có nhớ ngữ cảnh 10 lượt hội thoại gần nhất |
| **Xóa bộ nhớ trò chuyện AI** | `xóa lịch sử`, `clear`, `reset` | Xóa lịch sử hội thoại đang lưu trong RAM (`🧹 Wisp đã xóa lịch sử trò chuyện.`) |
| **Xem hướng dẫn** | `help`, `trợ giúp` | Hiển thị danh sách tóm tắt tất cả các lệnh |

---

## 📌 Cách ghim Bot lên thanh Taskbar để mở 1 chạm

Vì Windows không cho phép ghim trực tiếp file `.bat` lên thanh Taskbar, bạn có thể làm mẹo nhỏ sau (chỉ mất 30 giây):

1. Nhấp chuột phải vào file **`run.bat`** → chọn **Show more options** (trên Windows 11) → **Create shortcut** (hoặc **Send to** → **Desktop (create shortcut)**).
2. Nhấp chuột phải vào file **Shortcut** vừa tạo → chọn **Properties**.
3. Ở ô **Target**, bạn giữ nguyên đường dẫn cũ và thêm cụm **`cmd.exe /c `** vào ngay phía trước.
   - *Ví dụ:* `cmd.exe /c "C:\Users\ASUS\Documents\AGY\WindowLocalBot\run.bat"`
4. *(Tuỳ chọn)* Bấm nút **Change Icon...** để chọn biểu tượng đẹp mắt hơn, sau đó bấm **OK**.
5. Nhấp chuột phải vào Shortcut đó một lần nữa → chọn **Pin to taskbar** (Ghim vào thanh tác vụ) hoặc kéo thả trực tiếp xuống thanh Taskbar. Từ giờ bạn chỉ cần bấm 1 lần là Bot tự mở!

---

## 🛠️ Cách xử lý các lỗi thường gặp

### 1. Lỗi `"Không tìm thấy Python trên máy của bạn"`
- **Nguyên nhân:** Máy chưa cài Python, hoặc lúc cài bạn quên tick ô **"Add python.exe to PATH"**.
- **Cách sửa:** Chạy lại bộ cài đặt Python, chọn **Modify** → **Next** → tick chọn **"Add Python to environment variables"** → bấm **Install**. Hoặc gỡ ra cài lại và nhớ tick ô **"Add python.exe to PATH"** ngay ở bước đầu.

### 2. Lỗi `"Port 5000 đang bận"` (hoặc mở lên báo lỗi không chạy được server)
- **Nguyên nhân:** Bạn đã mở sẵn một cửa sổ `run.bat` trước đó chưa tắt, hoặc có một phần mềm khác trên máy đang dùng cổng 5000.
- **Cách sửa:**
  - Kiểm tra dưới thanh Taskbar xem có cửa sổ đen nào của Bot đang mở sẵn không, nếu có thì chỉ cần mở trình duyệt vào thẳng `http://127.0.0.1:5000` hoặc tắt cửa sổ cũ đi rồi chạy lại.
  - Nếu do phần mềm khác chiếm cổng 5000: tắt phần mềm đó hoặc mở file `app.py` bằng Notepad và đổi số `5000` thành số khác (ví dụ `5001`).

### 3. Lỗi mất mạng Wi-Fi khi hỏi thời tiết
- **Biểu hiện:** Bot báo *"⚠️ Không kết nối được mạng, kiểm tra Wi-Fi nhé."*
- **Nguyên nhân:** Tính năng xem thời tiết cần kết nối Internet để lấy dữ liệu mới nhất.
- **Lưu ý:** Khi máy mất mạng, bạn **vẫn có thể hỏi giờ giấc (`mấy giờ`, `giờ ở Tokyo`) và ngày tháng (`hôm nay`) bình thường** vì các tính năng này hoạt động hoàn toàn offline trên máy!
