"""
Flask backend cho Bot Local Web (Windows).
Cung cấp giao diện web tại / và các API /api/chat, /api/health.
"""
import sys
from flask import Flask, jsonify, render_template, request

from bot import GREETING, get_reply

# Đảm bảo console Windows hiển thị đúng tiếng Việt và emoji UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

app = Flask(__name__)
app.json.ensure_ascii = False


@app.route("/", methods=["GET"])
def index():
    """Trả về trang giao diện chat chính."""
    return render_template("index.html", greeting=GREETING)


@app.route("/api/chat", methods=["POST"])
def api_chat():
    """Nhận tin nhắn từ người dùng và trả về câu trả lời của bot."""
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            data = {}
        raw_msg = data.get("message", "")
        message = raw_msg if isinstance(raw_msg, str) else str(raw_msg or "")
        reply = get_reply(message)
        return jsonify({"reply": reply}), 200
    except Exception:
        return jsonify({"reply": "⚠️ Có lỗi gì đó, thử lại nhé."}), 200


@app.route("/api/health", methods=["GET"])
def api_health():
    """Kiểm tra trạng thái hoạt động của server."""
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    try:
        app.run(host="127.0.0.1", port=5000, debug=False)
    except OSError:
        print("Port 5000 đang bận, tắt app khác hoặc đổi port trong app.py")
        sys.exit(1)
