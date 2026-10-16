// Logic giao diện chat cho Bot Local Web
const FALLBACK_ERROR = "⚠️ Có lỗi gì đó, thử lại nhé.";

const chatEl = document.getElementById("chat");
const msgInput = document.getElementById("msg");
const sendBtn = document.getElementById("sendBtn");
const typingIndicator = document.getElementById("typingIndicator");
const quickBtns = document.querySelectorAll(".quick-btn");

let isSending = false;

/**
 * Tự cuộn khung chat xuống tin nhắn mới nhất.
 */
function scrollToBottom() {
  if (!chatEl) return;
  chatEl.scrollTop = chatEl.scrollHeight;
}

/**
 * Thêm một bong bóng tin nhắn vào khung chat.
 * Dùng textContent để chống XSS tuyệt đối; xuống dòng được giữ bởi CSS white-space: pre-wrap.
 * @param {string} text Nội dung tin nhắn
 * @param {"user" | "bot"} sender Người gửi
 */
function appendMessage(text, sender) {
  if (!chatEl) return;
  const bubble = document.createElement("div");
  bubble.className = `message ${sender === "user" ? "user-message" : "bot-message"}`;
  bubble.textContent = text;
  chatEl.appendChild(bubble);
  scrollToBottom();
}

/**
 * Bật/tắt trạng thái đang chờ phản hồi từ bot.
 * @param {boolean} sending
 */
function setSendingState(sending) {
  isSending = sending;
  if (sendBtn) sendBtn.disabled = sending;
  if (msgInput) msgInput.disabled = sending;
  quickBtns.forEach((btn) => {
    btn.disabled = sending;
  });

  if (typingIndicator) {
    if (sending) {
      typingIndicator.classList.remove("hidden");
    } else {
      typingIndicator.classList.add("hidden");
    }
  }
  scrollToBottom();
}

/**
 * Gửi tin nhắn tới backend /api/chat.
 * @param {string} [presetText] Văn bản truyền trực tiếp từ nút nhanh (tùy chọn)
 */
async function sendMessage(presetText) {
  if (isSending) return;

  const rawText = typeof presetText === "string" ? presetText : (msgInput ? msgInput.value : "");
  const message = rawText.trim();
  if (!message) {
    if (msgInput) msgInput.focus();
    return;
  }

  // Hiển thị bong bóng của user và xóa ô nhập
  appendMessage(message, "user");
  if (typeof presetText !== "string" && msgInput) {
    msgInput.value = "";
  } else if (msgInput && msgInput.value.trim() === message) {
    msgInput.value = "";
  }

  setSendingState(true);

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ message }),
    });

    if (!response.ok) {
      appendMessage(FALLBACK_ERROR, "bot");
      return;
    }

    const data = await response.json();
    const reply = data && typeof data.reply === "string" && data.reply.trim() !== ""
      ? data.reply
      : FALLBACK_ERROR;
    appendMessage(reply, "bot");
  } catch (_err) {
    appendMessage(FALLBACK_ERROR, "bot");
  } finally {
    setSendingState(false);
    if (msgInput) {
      msgInput.focus();
    }
  }
}

// Gắn sự kiện cho nút Gửi
if (sendBtn) {
  sendBtn.addEventListener("click", () => {
    sendMessage();
  });
}

// Gắn sự kiện nhấn phím Enter trong ô nhập (không reload trang)
if (msgInput) {
  msgInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      sendMessage();
    }
  });
}

// Gắn sự kiện cho các nút nhanh
quickBtns.forEach((btn) => {
  btn.addEventListener("click", () => {
    const text = btn.getAttribute("data-msg") || btn.textContent || "";
    sendMessage(text);
  });
});

// Expose hàm sendMessage ra global để tiện gọi trực tiếp nếu cần
window.sendMessage = sendMessage;
