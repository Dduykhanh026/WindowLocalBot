# 🤖 Bot Local Web (Windows)

Bot chat cá nhân chạy **100% trên máy tính Windows** của bạn với giao diện web gọn đẹp, mở ngay trong trình duyệt. Bạn có thể hỏi nhanh thời tiết hiện tại, dự báo ngày mai, giờ giấc các nước và ngày tháng mà không cần tài khoản hay cài đặt phức tạp.

---

## 🚀 Cách chạy nhanh (2 bước)

### Bước 1: Cài đặt Python (chỉ cần làm 1 lần đầu tiên)
1. Tải **Python 3.11 trở lên** tại trang chủ: [https://www.python.org/downloads/](https://www.python.org/downloads/)
2. Mở file vừa tải để cài đặt.
3. ⚠️ **QUAN TRỌNG:** Ở màn hình cài đặt đầu tiên, **bắt buộc phải tick vào ô `"Add python.exe to PATH"`** ở dưới cùng trước khi bấm **Install Now**.

### Bước 2: Mở Bot
- Nhấp đúp (**double-click**) vào file **`run.bat`** trong thư mục này.
- Lần đầu chạy, chương trình sẽ tự động tải thư viện cần thiết (mất khoảng vài giây).
- Sau đó trình duyệt sẽ **tự động mở** trang chat tại địa chỉ `http://127.0.0.1:5000`.
- *(Lưu ý: Giữ nguyên cửa sổ dòng lệnh màu đen trong lúc chat. Khi nào dùng xong bạn chỉ cần tắt cửa sổ đó đi).*

---

## 💬 Các câu lệnh có thể hỏi Bot

Bạn có thể bấm trực tiếp vào **4 nút gợi ý nhanh** trên giao diện hoặc gõ câu hỏi (hỗ trợ **cả tiếng Việt có dấu và không dấu**):

| Bạn muốn hỏi | Câu lệnh mẫu | Kết quả Bot trả về |
|---|---|---|
| **Thời tiết mặc định** | `thời tiết` | Thời tiết hiện tại ở Sài Gòn (nhiệt độ, cảm giác thực, độ ẩm, tốc độ gió) |
| **Thời tiết các tỉnh/thành** | `thời tiết Hà Nội`<br>`thoi tiet Da Nang`<br>`thời tiết Đà Lạt` | Thời tiết hiện tại tại địa điểm bạn muốn tra cứu (hỗ trợ cả gõ không dấu) |
| **Dự báo thời tiết ngày mai** | `thời tiết ngày mai Đà Lạt`<br>`thời tiết ngày mai` | Dự báo ngày mai gồm mô tả thời tiết, nhiệt độ thấp nhất/cao nhất và khả năng mưa (%) |
| **Giờ Việt Nam hiện tại** | `mấy giờ`, `giờ` | Giờ hiện tại ở Việt Nam kèm thứ và ngày tháng |
| **Giờ quốc tế** | `giờ ở Tokyo`, `giờ ở London`,<br>`giờ ở New York`, `giờ ở Seoul`,<br>`giờ ở Sydney`, `giờ ở Bangkok`,<br>`giờ ở Singapore`, `giờ ở Paris` | Giờ hiện tại ở các thành phố lớn trên thế giới (hỗ trợ thêm cả Mỹ, Anh, Nhật, Hàn, Pháp, Đức, Úc, Thái Lan, Trung Quốc...) |
| **Ngày tháng hôm nay** | `hôm nay`, `ngày mấy` | Thứ trong tuần và ngày/tháng/năm hiện tại |
| **Xem hướng dẫn** | `help`, `trợ giúp` | Hiển thị danh sách tóm tắt tất cả các lệnh |
| **Trò chuyện vui** | `hello`, `xin chào`,<br>`cảm ơn`, `tạm biệt` | Chào hỏi, cảm ơn và tạm biệt thân thiện |

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
