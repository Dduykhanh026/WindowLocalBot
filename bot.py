"""
Logic trả lời của bot — không phụ thuộc giao diện.
main.py (Tkinter) chỉ gọi get_reply() trong thread riêng.
"""
import re
import unicodedata
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from weather import get_current_weather, get_tomorrow_weather

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

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
    "• `help` — xem lại danh sách này"
)

GREETING = (
    "👋 Chào Nguyen! Mình là bot local chạy trên máy Windows này.\n"
    "Hỏi mình `thời tiết`, `mấy giờ`, `hôm nay`…\n"
    "Gõ `help` để xem tất cả lệnh nhé."
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
    t = re.sub(r"\b(o|o|tai|hien tai|bay gio|hom nay|ngay mai)\b", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" ,.?!")
    return t


def _world_clock(query_norm: str) -> str | None:
    for key, (label, tz) in WORLD_TZ.items():
        if key in query_norm:
            now = datetime.now(tz)
            return f"🕐 Bây giờ ở {label} là {now.strftime('%H:%M')} — {WEEKDAY_VI[now.weekday()]}, {now.strftime('%d/%m/%Y')}."
    return None


def get_reply(message: str) -> str:
    raw = message.strip()
    if not raw:
        return "Gõ gì đó đi, vd: `thời tiết`, `mấy giờ` 😄"
    n = _norm(raw)

    # --- Help / chào ---
    if n in ("help", "tro giup", "giup", "lenh", "menu", "?"):
        return HELP_TEXT
    if n in ("hi", "hello", "hey", "chao", "xin chao", "alo"):
        return "👋 Chào Nguyen! Hỏi mình `thời tiết` hay `mấy giờ` đi."

    # --- Thời tiết (ưu tiên trước, vì câu có thể chứa cả 'ngày mai'...) ---
    if "thoi tiet" in n or "thoi tiet" in n or "weather" in n or "nhiet do" in n:
        is_tomorrow = "ngay mai" in n or "tomorrow" in n
        city = _extract_city_after(n, ["thoi tiet", "weather", "nhiet do"])
        # Nếu không có thành phố -> mặc định Sài Gòn
        if not city:
            city = "Ho Chi Minh"
        if is_tomorrow:
            return get_tomorrow_weather(city)
        return get_current_weather(city)

    # --- Giờ giấc ---
    if "may gio" in n or n in ("gio", "gio roi", "gio may gio", "time", "bay gio may gio"):
        now = datetime.now(VN_TZ)
        return (
            f"🕐 Bây giờ là {now.strftime('%H:%M:%S')} — "
            f"{WEEKDAY_VI[now.weekday()]}, {now.strftime('%d/%m/%Y')} (giờ Việt Nam)."
        )
    if "gio o" in n or "gio tai" in n or ("gio" in n and any(k in n for k in WORLD_TZ)):
        world = _world_clock(n)
        if world:
            return world
        now = datetime.now(VN_TZ)
        return f"🕐 Bây giờ là {now.strftime('%H:%M:%S')} (giờ Việt Nam). Gõ `giờ ở Tokyo` để xem giờ thành phố khác nhé."

    # --- Ngày tháng ---
    if "hom nay" in n or "ngay may" in n or "ngay hom nay" in n or "thu may" in n or n in ("ngay", "date", "today"):
        now = datetime.now(VN_TZ)
        return f"📅 Hôm nay là {WEEKDAY_VI[now.weekday()]}, ngày {now.strftime('%d/%m/%Y')}."

    # --- Cảm ơn / tạm biệt ---
    if "cam on" in n or "thank" in n:
        return "Không có chi 😊 Cần gì cứ hỏi mình nhé."
    if "tam biet" in n or "bye" in n:
        return "👋 Bye Nguyen, hẹn gặp lại!"

    # --- Fallback ---
    return (
        "Hmm, mình chưa hiểu câu đó 😅\n"
        "Thử: `thời tiết Hà Nội`, `mấy giờ`, `hôm nay` — hoặc gõ `help` để xem tất cả lệnh."
    )
