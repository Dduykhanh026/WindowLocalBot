"""
Flask backend cho Bot Local Web (Windows).
Cung cấp giao diện web tại / và các API /api/chat, /api/health.
"""
import json
import sys
from flask import Flask, Response, jsonify, render_template, request, stream_with_context

from bot import GREETING, get_reply, stream_reply

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


@app.route("/api/chat/stream", methods=["POST"])
def api_chat_stream():
    """Stream NDJSON gồm các pha suy nghĩ thực tế của backend/LLM và từng token phản hồi."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    raw_msg = data.get("message", "")
    message = raw_msg if isinstance(raw_msg, str) else str(raw_msg or "")

    def generate():
        try:
            for item in stream_reply(message):
                if isinstance(item, dict):
                    yield json.dumps(item, ensure_ascii=False) + "\n"
                elif isinstance(item, str):
                    yield item if item.endswith("\n") else item + "\n"
        except Exception:
            yield json.dumps(
                {"type": "token", "delta": "⚠️ Có lỗi gì đó, thử lại nhé."},
                ensure_ascii=False,
            ) + "\n"
            yield json.dumps({"type": "done"}, ensure_ascii=False) + "\n"

    return Response(
        stream_with_context(generate()),
        mimetype="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


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
