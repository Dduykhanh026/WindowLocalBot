"""
Logic trả lời của bot — không phụ thuộc giao diện.
Kết hợp Fast-path tra cứu tức thì (giờ/ngày/thời tiết chuẩn SPEC)
và AI Local qua Ollama (qwen2.5:3b) cho hội thoại tự nhiên, tư vấn, viết code.
"""
import json
import re
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

from weather import CITY_ALIASES, get_current_weather, get_tomorrow_weather

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

OLLAMA_CHAT_URL = "http://127.0.0.1:11434/api/chat"
OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"
DEFAULT_MODEL = "qwen2.5:3b"
MAX_HISTORY_TURNS = 10  # Giữ tối đa 10 lượt hội thoại gần nhất (20 messages)

SYSTEM_PROMPT = (
    "Bạn là Wisp, trợ lý AI cá nhân thông minh chạy local 100% trên máy Windows "
    "của Nguyen (lập trình viên iOS/SwiftUI và Flutter). "
    "QUY TẮC BẮT BUỘC: Luôn trả lời 100% bằng tiếng Việt chuẩn xác, tự nhiên, ngắn gọn, súc tích. "
    "TUYỆT ĐỐI KHÔNG dùng chữ Hán (tiếng Trung) trong câu trả lời. "
    "Khi viết code hoặc lệnh thì bọc trong dấu backtick."
)

OLLAMA_WAITING_MSG = (
    "⏳ Wisp đang khởi động bộ não AI local (Ollama - qwen2.5:3b) hoặc đang tải model. "
    "Trong lúc chờ, bạn vẫn có thể hỏi `thời tiết`, `mấy giờ`, `hôm nay` nhé!"
)

# Lịch sử hội thoại ngắn lưu trong RAM
_CHAT_HISTORY: list[dict[str, str]] = []

WEEKDAY_VI = [
    "Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm",
    "Thứ Sáu", "Thứ Bảy", "Chủ Nhật",
]

# Giờ thế giới — gõ: "giờ ở Tokyo", "mấy giờ ở London"
WORLD_TZ = {
    "tokyo": ("Tokyo", ZoneInfo("Asia/Tokyo")),
    "nhat": ("Tokyo", ZoneInfo("Asia/Tokyo")),
    "seoul": ("Seoul", ZoneInfo("Asia/Seoul")),
    "han quoc": ("Seoul", ZoneInfo("Asia/Seoul")),
    "bangkok": ("Bangkok", ZoneInfo("Asia/Bangkok")),
    "thai lan": ("Bangkok", ZoneInfo("Asia/Bangkok")),
    "singapore": ("Singapore", ZoneInfo("Asia/Singapore")),
    "sing": ("Singapore", ZoneInfo("Asia/Singapore")),
    "london": ("London", ZoneInfo("Europe/London")),
    "anh": ("London", ZoneInfo("Europe/London")),
    "paris": ("Paris", ZoneInfo("Europe/Paris")),
    "phap": ("Paris", ZoneInfo("Europe/Paris")),
    "berlin": ("Berlin", ZoneInfo("Europe/Berlin")),
    "duc": ("Berlin", ZoneInfo("Europe/Berlin")),
    "new york": ("New York", ZoneInfo("America/New_York")),
    "my": ("New York", ZoneInfo("America/New_York")),
    "los angeles": ("Los Angeles", ZoneInfo("America/Los_Angeles")),
    "sydney": ("Sydney", ZoneInfo("Australia/Sydney")),
    "uc": ("Sydney", ZoneInfo("Australia/Sydney")),
    "dubai": ("Dubai", ZoneInfo("Asia/Dubai")),
}

HELP_TEXT = (
    "🤖 Mình biết làm những thứ này:\n"
    "• `thời tiết` — thời tiết hiện tại ở Sài Gòn\n"
    "• `thời tiết Hà Nội` — thời tiết ở thành phố bất kỳ\n"
    "• `thời tiết ngày mai Đà Lạt` — dự báo ngày mai\n"
    "• `mấy giờ` / `giờ` — giờ hiện tại\n"
    "• `giờ ở Tokyo` — giờ ở thành phố khác\n"
    "• `hôm nay` / `ngày mấy` — ngày tháng hôm nay\n"
    "• `xóa lịch sử` / `clear` — xóa lịch sử trò chuyện AI\n"
    "• `help` — xem lại danh sách này\n"
    "• 💬 Hoặc hỏi bất kỳ câu hỏi tự nhiên, tư vấn thời tiết, viết code SwiftUI/Flutter (AI Local Qwen 2.5)"
)

GREETING = (
    "👋 Chào Nguyen! Mình là Wisp — bot local chạy trên máy Windows này.\n"
    "Hỏi mình `thời tiết`, `mấy giờ`, `hôm nay`…\n"
    "Gõ `help` để xem tất cả lệnh nhé."
)

# Từ khóa nhận diện câu hỏi mở rộng / tư vấn để chuyển sang AI LLM thay vì chỉ tra cứu ngắn
_ADVISORY_KEYWORDS = (
    "mac gi", "an gi", "uong gi", "lam gi", "di dau", "di choi",
    "co nen", "nen ", "hop", "tu van", "goi y", "trang phuc",
    "mang gi", "mang o", "ao mua", "tai sao", "vi sao", "ra sao",
    "lam sao", "the nao", "buon", "vui", "tho ", "ke ", "viet ",
    "code", "swift", "flutter", "python", "la gi", "la ai",
)

# Từ khóa nhận diện câu hỏi có liên quan đến thời tiết để đính kèm dữ liệu Open-Meteo vào prompt AI
_WEATHER_TOPIC_KEYWORDS = (
    "thoi tiet", "weather", "nhiet do", "mua", "nang",
    "lanh", "nong", "troi", "am uot", "suong mu", "giong bao",
)


def _strip_accents(s: str) -> str:
    """'thời tiết' -> 'thoi tiet' để so khớp không cần gõ dấu."""
    s = s.replace("Đ", "D").replace("đ", "d").replace("Ð", "D").replace("ð", "d")
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def _norm(s: str) -> str:
    return _strip_accents(s.lower()).strip()


def _extract_city_after(text_norm: str, keywords: list[str]) -> str:
    """Lấy phần thành phố sau từ khóa, vd 'thoi tiet ha noi' -> 'ha noi'."""
    t = text_norm
    for kw in keywords:
        if kw in t:
            t = t.split(kw, 1)[1]
            break
    # Bỏ các từ thừa
    t = re.sub(r"\b(o|tai|hien tai|bay gio|hom nay|ngay mai|tomorrow)\b", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" ,.?!")
    return t


def _detect_city_in_text(text_norm: str) -> str:
    """Tìm tên thành phố trong câu hỏi tự nhiên để lấy thời tiết cho AI context."""
    # Ưu tiên khớp các alias dài trước (vd 'tp ho chi minh' trước 'ho chi minh')
    for alias in sorted(CITY_ALIASES.keys(), key=len, reverse=True):
        if re.search(rf"\b{re.escape(alias)}\b", text_norm):
            return alias
    extracted = _extract_city_after(text_norm, ["thoi tiet", "weather", "nhiet do"])
    if extracted and len(extracted.split()) <= 4:
        return extracted
    return "Ho Chi Minh"


def _world_clock(query_norm: str) -> str | None:
    for key, (label, tz) in WORLD_TZ.items():
        if re.search(rf"\b{re.escape(key)}\b", query_norm):
            now = datetime.now(tz)
            return f"🕐 Bây giờ ở {label} là {now.strftime('%H:%M')} — {WEEKDAY_VI[now.weekday()]}, {now.strftime('%d/%m/%Y')}."
    return None


def _is_short_weather_command(text_norm: str) -> bool:
    """Kiểm tra xem câu có phải lệnh tra cứu thời tiết ngắn chuẩn SPEC hay không."""
    if not any(kw in text_norm for kw in ("thoi tiet", "weather", "nhiet do")):
        return False
    if any(adv in text_norm for adv in _ADVISORY_KEYWORDS):
        return False
    if "?" in text_norm or len(text_norm.split()) > 8:
        return False
    return True


def _resolve_ollama_model() -> str | None:
    """Kiểm tra model trong Ollama. Ưu tiên DEFAULT_MODEL, nếu chưa có thì lấy model đầu tiên có sẵn."""
    req = urllib.request.Request(OLLAMA_TAGS_URL, method="GET")
    with urllib.request.urlopen(req, timeout=3) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    models = data.get("models") or []
    names = [m.get("name", "") for m in models if isinstance(m, dict) and m.get("name")]
    if not names:
        return None
    for name in names:
        if name == DEFAULT_MODEL or name.startswith(f"{DEFAULT_MODEL}:"):
            return name
    return names[0]


def _build_system_context(text_norm: str) -> str:
    """Tạo system prompt kèm ngữ cảnh thời gian thực và thời tiết thực tế (nếu câu hỏi nhắc tới thời tiết)."""
    now = datetime.now(VN_TZ)
    ctx_lines = [
        SYSTEM_PROMPT,
        f"\n[Ngữ cảnh thời gian thực]: Bây giờ là {now.strftime('%H:%M:%S')} — {WEEKDAY_VI[now.weekday()]}, ngày {now.strftime('%d/%m/%Y')} (giờ Việt Nam).",
    ]
    if any(w in text_norm for w in _WEATHER_TOPIC_KEYWORDS):
        city = _detect_city_in_text(text_norm)
        is_tomorrow = "ngay mai" in text_norm or "tomorrow" in text_norm
        weather_info = get_tomorrow_weather(city) if is_tomorrow else get_current_weather(city)
        ctx_lines.append(
            f"[Dữ liệu thời tiết thực tế từ Open-Meteo]:\n{weather_info}\n"
            "(Hãy dùng chính xác số liệu thời tiết trên để trả lời hoặc tư vấn cho Nguyen)."
        )
    return "\n".join(ctx_lines)


def _ask_ollama(raw_message: str, text_norm: str) -> str:
    """Gửi câu hỏi sang Ollama local API kèm lịch sử hội thoại và ngữ cảnh thực tế."""
    try:
        model = _resolve_ollama_model()
        if not model:
            return OLLAMA_WAITING_MSG

        system_content = _build_system_context(text_norm)
        max_msgs = MAX_HISTORY_TURNS * 2
        messages = (
            [{"role": "system", "content": system_content}]
            + _CHAT_HISTORY[-max_msgs:]
            + [{"role": "user", "content": raw_message}]
        )
        payload = json.dumps(
            {
                "model": model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": 0.35},
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            OLLAMA_CHAT_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        reply = (data.get("message") or {}).get("content", "").strip()
        if not reply:
            return OLLAMA_WAITING_MSG

        _CHAT_HISTORY.append({"role": "user", "content": raw_message})
        _CHAT_HISTORY.append({"role": "assistant", "content": reply})
        if len(_CHAT_HISTORY) > max_msgs:
            del _CHAT_HISTORY[:-max_msgs]

        return reply
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError):
        return OLLAMA_WAITING_MSG
    except Exception:
        return OLLAMA_WAITING_MSG


def get_reply(message: str) -> str:
    raw = message.strip()
    if not raw:
        return "Gõ gì đó đi, vd: `thời tiết`, `mấy giờ` 😄"
    n = _norm(raw)
    n_clean = n.strip(" ,.?!")

    # --- Xóa lịch sử hội thoại ---
    if n_clean in ("xoa lich su", "clear", "reset", "xoa chat"):
        _CHAT_HISTORY.clear()
        return "🧹 Wisp đã xóa lịch sử trò chuyện."

    # --- Help / chào ngắn (Fast-path) ---
    if n_clean in ("help", "tro giup", "giup", "lenh", "menu", "?"):
        return HELP_TEXT
    if n_clean in ("hi", "hello", "hey", "chao", "xin chao", "alo", "wisp"):
        return "👋 Chào Nguyen! Mình là Wisp. Hỏi mình `thời tiết` hay `mấy giờ` đi."

    # --- Thời tiết tra cứu ngắn (Fast-path chuẩn SPEC) ---
    if _is_short_weather_command(n):
        is_tomorrow = "ngay mai" in n or "tomorrow" in n
        city = _extract_city_after(n, ["thoi tiet", "weather", "nhiet do"])
        if not city:
            city = "Ho Chi Minh"
        if is_tomorrow:
            return get_tomorrow_weather(city)
        return get_current_weather(city)

    # --- Giờ giấc tra cứu ngắn (Fast-path chuẩn SPEC) ---
    if n_clean in (
        "may gio", "may gio roi", "gio", "gio roi",
        "gio may gio", "time", "bay gio may gio", "bay gio la may gio",
    ):
        now = datetime.now(VN_TZ)
        return (
            f"🕐 Bây giờ là {now.strftime('%H:%M:%S')} — "
            f"{WEEKDAY_VI[now.weekday()]}, {now.strftime('%d/%m/%Y')} (giờ Việt Nam)."
        )
    if (
        ("gio o" in n_clean or "gio tai" in n_clean or ("gio" in n_clean and any(k in n_clean for k in WORLD_TZ)))
        and len(n_clean.split()) <= 7
        and not any(adv in n_clean for adv in _ADVISORY_KEYWORDS)
    ):
        world = _world_clock(n_clean)
        if world:
            return world
        now = datetime.now(VN_TZ)
        return f"🕐 Bây giờ là {now.strftime('%H:%M:%S')} (giờ Việt Nam). Gõ `giờ ở Tokyo` để xem giờ thành phố khác nhé."

    # --- Ngày tháng tra cứu ngắn (Fast-path chuẩn SPEC) ---
    if n_clean in (
        "hom nay", "ngay may", "ngay hom nay", "hom nay ngay may",
        "hom nay la ngay may", "thu may", "hom nay thu may",
        "hom nay la thu may", "ngay", "date", "today",
    ):
        now = datetime.now(VN_TZ)
        return f"📅 Hôm nay là {WEEKDAY_VI[now.weekday()]}, ngày {now.strftime('%d/%m/%Y')}."

    # --- Cảm ơn / tạm biệt ngắn (Fast-path chuẩn SPEC) ---
    if n_clean in ("cam on", "cam on nhe", "cam on ban", "thank", "thanks", "thank you"):
        return "Không có chi 😊 Cần gì cứ hỏi mình nhé."
    if n_clean in ("tam biet", "bye", "goodbye", "bai bai", "hen gap lai"):
        return "👋 Bye Nguyen, hẹn gặp lại!"

    # --- Mọi câu hỏi tự nhiên, tư vấn thời tiết, trò chuyện, viết code -> AI Local (Ollama) ---
    return _ask_ollama(raw, n)


def stream_reply(message: str):
    """
    Generator phát luồng sự kiện thời gian thực (NDJSON) phản ánh chính xác
    tiến trình xử lý ở backend & luồng sinh token từ GPU Ollama.
    """
    import time

    raw = message.strip()
    if not raw:
        yield {"type": "state", "state": "breathing", "label": "Sẵn sàng"}
        yield {"type": "token", "delta": "Gõ gì đó đi, vd: `thời tiết`, `mấy giờ` 😄"}
        yield {"type": "done"}
        return

    n = _norm(raw)
    n_clean = n.strip(" ,.?!")

    # Giai đoạn 1: Mở kết nối & kiểm tra bộ nhớ ngữ cảnh
    yield {"type": "state", "state": "connecting", "label": "Đang nạp ngữ cảnh…"}

    # Xóa bộ nhớ hội thoại -> trạng thái shaping (biến đổi/tái cấu trúc)
    if n_clean in ("xoa lich su", "clear", "reset", "xoa chat"):
        yield {"type": "state", "state": "shaping", "label": "Đang làm mới bộ nhớ hội thoại…"}
        time.sleep(0.35)
        _CHAT_HISTORY.clear()
        yield {"type": "token", "delta": "🧹 Wisp đã xóa lịch sử trò chuyện."}
        yield {"type": "done"}
        return

    # Fast-path: Thời tiết ngắn -> kích hoạt searching vì thực sự gọi API Open-Meteo
    if _is_short_weather_command(n):
        is_tomorrow = "ngay mai" in n or "tomorrow" in n
        city = _extract_city_after(n, ["thoi tiet", "weather", "nhiet do"]) or "Ho Chi Minh"
        yield {"type": "state", "state": "searching", "label": "Đang truy xuất dữ liệu Open-Meteo…"}
        t0 = time.time()
        res = get_tomorrow_weather(city) if is_tomorrow else get_current_weather(city)
        elapsed = time.time() - t0
        if elapsed < 0.45:
            time.sleep(0.45 - elapsed)
        yield {"type": "state", "state": "composing", "label": "Đang phản hồi…"}
        for chunk in re.findall(r"\S+\s*", res):
            yield {"type": "token", "delta": chunk}
            time.sleep(0.015)
        yield {"type": "done"}
        return

    # Fast-path: Đồng hồ hệ thống / Giờ quốc tế / Ngày tháng -> trạng thái working
    fast_reply = None
    if n_clean in ("help", "tro giup", "giup", "lenh", "menu", "?"):
        fast_reply = HELP_TEXT
    elif n_clean in ("hi", "hello", "hey", "chao", "xin chao", "alo", "wisp"):
        fast_reply = "👋 Chào Nguyen! Mình là Wisp. Hỏi mình `thời tiết` hay `mấy giờ` đi."
    elif n_clean in (
        "may gio", "may gio roi", "gio", "gio roi",
        "gio may gio", "time", "bay gio may gio", "bay gio la may gio",
    ):
        now = datetime.now(VN_TZ)
        fast_reply = (
            f"🕐 Bây giờ là {now.strftime('%H:%M:%S')} — "
            f"{WEEKDAY_VI[now.weekday()]}, {now.strftime('%d/%m/%Y')} (giờ Việt Nam)."
        )
    elif (
        ("gio o" in n_clean or "gio tai" in n_clean or ("gio" in n_clean and any(k in n_clean for k in WORLD_TZ)))
        and len(n_clean.split()) <= 7
        and not any(adv in n_clean for adv in _ADVISORY_KEYWORDS)
    ):
        world = _world_clock(n_clean)
        if world:
            fast_reply = world
        else:
            now = datetime.now(VN_TZ)
            fast_reply = f"🕐 Bây giờ là {now.strftime('%H:%M:%S')} (giờ Việt Nam). Gõ `giờ ở Tokyo` để xem giờ thành phố khác nhé."
    elif n_clean in (
        "hom nay", "ngay may", "ngay hom nay", "hom nay ngay may",
        "hom nay la ngay may", "thu may", "hom nay thu may",
        "hom nay la thu may", "ngay", "date", "today",
    ):
        now = datetime.now(VN_TZ)
        fast_reply = f"📅 Hôm nay là {WEEKDAY_VI[now.weekday()]}, ngày {now.strftime('%d/%m/%Y')}."
    elif n_clean in ("cam on", "cam on nhe", "cam on ban", "thank", "thanks", "thank you"):
        fast_reply = "Không có chi 😊 Cần gì cứ hỏi mình nhé."
    elif n_clean in ("tam biet", "bye", "goodbye", "bai bai", "hen gap lai"):
        fast_reply = "👋 Bye Nguyen, hẹn gặp lại!"

    if fast_reply is not None:
        yield {"type": "state", "state": "working", "label": "Đang xử lý tác vụ nội bộ…"}
        time.sleep(0.38)
        yield {"type": "state", "state": "composing", "label": "Đang phản hồi…"}
        for chunk in re.findall(r"\S+\s*", fast_reply):
            yield {"type": "token", "delta": chunk}
            time.sleep(0.014)
        yield {"type": "done"}
        return

    # Luồng AI Local (Ollama Streaming thật từ GPU)
    try:
        model = _resolve_ollama_model()
        if not model:
            yield {"type": "state", "state": "shaping", "label": "Đang khởi tạo model…"}
            yield {"type": "token", "delta": OLLAMA_WAITING_MSG}
            yield {"type": "done"}
            return

        now = datetime.now(VN_TZ)
        ctx_lines = [
            SYSTEM_PROMPT,
            f"\n[Ngữ cảnh thời gian thực]: Bây giờ là {now.strftime('%H:%M:%S')} — {WEEKDAY_VI[now.weekday()]}, ngày {now.strftime('%d/%m/%Y')} (giờ Việt Nam).",
        ]

        # Nếu cần gọi Tool thời tiết thực tế để bổ sung dữ liệu vào prompt
        if any(w in n for w in _WEATHER_TOPIC_KEYWORDS):
            yield {"type": "state", "state": "searching", "label": "Đang lấy dữ liệu thời tiết thực tế…"}
            city = _detect_city_in_text(n)
            is_tomorrow = "ngay mai" in n or "tomorrow" in n
            weather_info = get_tomorrow_weather(city) if is_tomorrow else get_current_weather(city)
            ctx_lines.append(
                f"[Dữ liệu thời tiết thực tế từ Open-Meteo]:\n{weather_info}\n"
                "(Hãy dùng chính xác số liệu thời tiết trên để trả lời hoặc tư vấn cho Nguyen)."
            )
            yield {"type": "state", "state": "weaving", "label": "Đang đan ghép dữ liệu vào ngữ cảnh AI…"}
            time.sleep(0.28)

        system_content = "\n".join(ctx_lines)
        max_msgs = MAX_HISTORY_TURNS * 2
        messages = (
            [{"role": "system", "content": system_content}]
            + _CHAT_HISTORY[-max_msgs:]
            + [{"role": "user", "content": raw}]
        )

        # Giai đoạn Prefill / Suy luận trên GPU trước khi sinh token đầu tiên
        yield {"type": "state", "state": "solving", "label": "Đang suy luận trên GPU…"}

        payload = json.dumps(
            {
                "model": model,
                "messages": messages,
                "stream": True,
                "options": {"temperature": 0.35},
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            OLLAMA_CHAT_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        full_reply_parts: list[str] = []
        with urllib.request.urlopen(req, timeout=60) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                chunk_data = json.loads(line)
                delta = (chunk_data.get("message") or {}).get("content", "")
                if delta:
                    full_reply_parts.append(delta)
                    yield {"type": "token", "delta": delta}
                if chunk_data.get("done"):
                    break

        full_reply = "".join(full_reply_parts).strip()
        if not full_reply:
            yield {"type": "token", "delta": OLLAMA_WAITING_MSG}
        else:
            _CHAT_HISTORY.append({"role": "user", "content": raw})
            _CHAT_HISTORY.append({"role": "assistant", "content": full_reply})
            if len(_CHAT_HISTORY) > max_msgs:
                del _CHAT_HISTORY[:-max_msgs]

        yield {"type": "done"}
    except Exception:
        yield {"type": "token", "delta": OLLAMA_WAITING_MSG}
        yield {"type": "done"}


