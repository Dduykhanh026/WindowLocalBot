"""
Logic trả lời của bot — không phụ thuộc giao diện.
Kết hợp Fast-path tra cứu tức thì (giờ/ngày/thời tiết chuẩn SPEC),
Ưu tiên AI Online qua Google AI Studio (Gemini API) khi có Internet,
và tự động Fallback về AI Local qua Ollama (qwen2.5:3b) khi mất mạng.
"""
import json
import os
import re
import socket
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from weather import CITY_ALIASES, get_current_weather, get_tomorrow_weather

try:
    VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
except Exception:
    VN_TZ = timezone(timedelta(hours=7))

def _load_local_dotenv(dotenv_path: str | None = None) -> None:
    """Đọc file .env cục bộ bằng stdlib (nếu có) để tránh hardcode khóa API trong mã nguồn."""
    env_path = dotenv_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.isfile(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8-sig") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                if k.startswith("export "):
                    k = k[7:].strip()
                v = v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
    except OSError:
        pass


_load_local_dotenv()

GEMINI_API_KEY = (
    os.environ.get("GEMINI_API_KEY")
    or os.environ.get("GOOGLE_API_KEY")
    or ""
).strip()
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_MODELS = (
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-3.1-flash-lite-preview",
)
# Lưu thời điểm (monotonic) mà một model Gemini đang bị giới hạn quota (429) hoặc quá tải (503) để bỏ qua ngay lập tức (0ms)
_GEMINI_COOLDOWN_UNTIL: dict[str, float] = {}
# Ngắt mạch kết nối nhanh khi mất mạng toàn cục (DNS/Socket/Unreachable) để failover sang Ollama trong <1s
_GEMINI_NETWORK_OFFLINE_UNTIL: float = 0.0

OLLAMA_CHAT_URL = "http://127.0.0.1:11434/api/chat"
OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"
DEFAULT_MODEL = "qwen2.5:3b"
_CACHED_OLLAMA_MODEL: str | None = None
_CACHED_OLLAMA_EXPIRES: float = 0.0

MAX_HISTORY_TURNS = 10  # Giữ tối đa 10 lượt hội thoại gần nhất (20 messages)

SYSTEM_PROMPT = (
    "Bạn là Wisp, trợ lý AI cá nhân thông minh, tinh tế và nhạy bén chạy trên máy Windows.\n"
    "QUY TẮC CỐT LÕI:\n"
    "1. Luôn trả lời 100% bằng tiếng Việt chuẩn xác, tự nhiên, giàu tính thực tế (chỉ giữ tiếng Anh cho thuật ngữ lập trình, tên công nghệ hoặc tên riêng). TUYỆT ĐỐI KHÔNG dùng chữ Hán (tiếng Trung).\n"
    "2. Hiểu sâu ý định người dùng: phân tích đúng ngữ cảnh (thời gian thực, thời tiết thực tế nếu có, và lịch sử hội thoại trước đó) để đưa ra câu trả lời thông minh, hợp lý và hữu ích nhất.\n"
    "3. Không tự ý nhắc tới nghề nghiệp hay kỹ năng lập trình của người dùng trừ khi câu hỏi thực sự hỏi về lập trình.\n"
    "4. Khi viết tên lệnh, từ khóa kỹ thuật ngắn hoặc mã nguồn thì bọc trong dấu backtick."
)

OLLAMA_WAITING_MSG = (
    "⏳ Wisp đang khởi động bộ não AI local (Ollama - qwen2.5:3b) hoặc đang tải model. "
    "Trong lúc chờ, bạn vẫn có thể hỏi `thời tiết`, `mấy giờ`, `hôm nay` nhé!"
)

# Lịch sử hội thoại ngắn lưu trong RAM với khóa bảo vệ an toàn đa luồng (thread-safety)
_CHAT_HISTORY: list[dict[str, str]] = []
_CHAT_HISTORY_LOCK = threading.Lock()

WEEKDAY_VI = [
    "Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm",
    "Thứ Sáu", "Thứ Bảy", "Chủ Nhật",
]

# Giờ thế giới — gõ: "giờ ở Tokyo", "mấy giờ ở London", "giờ ở Nhật Bản"
WORLD_TZ = {
    "tokyo": ("Tokyo", ZoneInfo("Asia/Tokyo")),
    "nhat ban": ("Tokyo", ZoneInfo("Asia/Tokyo")),
    "seoul": ("Seoul", ZoneInfo("Asia/Seoul")),
    "han quoc": ("Seoul", ZoneInfo("Asia/Seoul")),
    "bangkok": ("Bangkok", ZoneInfo("Asia/Bangkok")),
    "thai lan": ("Bangkok", ZoneInfo("Asia/Bangkok")),
    "singapore": ("Singapore", ZoneInfo("Asia/Singapore")),
    "london": ("London", ZoneInfo("Europe/London")),
    "nuoc anh": ("London", ZoneInfo("Europe/London")),
    "paris": ("Paris", ZoneInfo("Europe/Paris")),
    "nuoc phap": ("Paris", ZoneInfo("Europe/Paris")),
    "phap": ("Paris", ZoneInfo("Europe/Paris")),
    "berlin": ("Berlin", ZoneInfo("Europe/Berlin")),
    "nuoc duc": ("Berlin", ZoneInfo("Europe/Berlin")),
    "new york": ("New York", ZoneInfo("America/New_York")),
    "nuoc my": ("New York", ZoneInfo("America/New_York")),
    "hoa ky": ("New York", ZoneInfo("America/New_York")),
    "los angeles": ("Los Angeles", ZoneInfo("America/Los_Angeles")),
    "sydney": ("Sydney", ZoneInfo("Australia/Sydney")),
    "nuoc uc": ("Sydney", ZoneInfo("Australia/Sydney")),
    "australia": ("Sydney", ZoneInfo("Australia/Sydney")),
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
    "👋 Chào bạn! Mình là Wisp — bot local chạy trên máy Windows này.\n"
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

# Từ khóa nhận diện câu hỏi có liên quan đến thời tiết / ra ngoài / trang phục để đính kèm dữ liệu Open-Meteo vào prompt AI
_WEATHER_TOPIC_KEYWORDS = (
    "thoi tiet", "weather", "nhiet do", "du bao", "mua", "nang",
    "lanh", "nong", "mat", "oi buc", "troi", "am uot", "suong mu", "giong bao",
    "mac gi", "trang phuc", "outfit", "mang o", "ao mua", "ao khoac",
    "ra ngoai", "ra duong", "di dao", "di choi", "di ca phe",
    "bao nhieu do", "may do",
)


def _is_weather_topic(raw_text: str, text_norm: str) -> bool:
    """
    Nhận diện câu hỏi có chủ đề thời tiết bằng regex ranh giới từ và đối chiếu tiếng Việt có dấu.
    Tránh tuyệt đối so khớp chuỗi con trần khiến các từ 'tính năng', 'mất ngủ', 'mua sắm' bị bắt nhầm.
    """
    raw_lower = (raw_text or "").lower()
    n = text_norm or _norm(raw_lower)

    # 1. Các cụm từ ghép thời tiết / hoạt động trang phục rõ ràng
    safe_phrases = (
        r"\bthời\s+tiết\b", r"\bweather\b", r"\bnhiệt\s+độ\b", r"\bdự\s+báo\b",
        r"\boi\s+bức\b", r"\bẩm\s+ướt\b", r"\bsương\s+mù\b", r"\bgiông\s+bão\b",
        r"\bnhiều\s+mây\b", r"\bu\s+ám\b", r"\bâm\s+u\b", r"\bmây\s+mù\b", r"\bquang\s+mây\b",
        r"\bmặc\s+gì\b", r"\btrang\s+phục\b", r"\boutfit\b", r"\bmang\s+ô\b", r"\bmang\s+dù\b",
        r"\báo\s+mưa\b", r"\báo\s+khoác\b", r"\bra\s+ngoài\b", r"\bra\s+đường\b",
        r"\bđi\s+dạo\b", r"\bđi\s+chơi\b", r"\bđi\s+cà\s+phê\b", r"\bbao\s+nhiêu\s+độ\b", r"\bmấy\s+độ\b",
    )
    for p in safe_phrases:
        if re.search(p, raw_lower):
            return True

    # 2. Kiểm tra không dấu cho các cụm an toàn
    safe_norm_phrases = (
        r"\bthoi tiet\b", r"\bnhiet do\b", r"\bdu bao\b", r"\boi buc\b", r"\bam uot\b",
        r"\bsuong mu\b", r"\bgiong bao\b", r"\bnhieu may\b", r"\bu am\b", r"\bam u\b", r"\bmay mu\b",
        r"\bmac gi\b", r"\btrang phuc\b", r"\boutfit\b",
        r"\bmang o\b", r"\bmang du\b", r"\bao mua\b", r"\bao khoac\b", r"\bra ngoai\b",
        r"\bra duong\b", r"\bdi dao\b", r"\bdi choi\b", r"\bdi ca phe\b", r"\bbao nhieu do\b", r"\bmay do\b",
        r"\btroi mua\b", r"\btroi nang\b", r"\btroi mat\b", r"\btroi lanh\b", r"\btroi nong\b",
    )
    for p in safe_norm_phrases:
        if re.search(p, n):
            return True

    # 3. Phân biệt các từ đơn dễ va chạm:
    # - "nắng": loại trừ "tính năng", "chức năng", "khả năng", "năng lượng", "kỹ năng", "tài năng"
    if re.search(r"\bnắng\b", raw_lower):
        return True
    if re.search(r"\bnang\b", n) and not re.search(r"\b(?:tinh|chuc|kha|tiem|ky|tai)\s+nang\b", n) and not re.search(r"\bnang\s+(?:luong|suat|khieu)\b", n):
        if re.search(r"\b(?:troi|co|ngay|ngoai|hom nay|ngay mai|rat|hoi)\s+nang\b|\bnang\s+(?:to|gat|dep|chot|hanh|nhe|nong)\b", n):
            return True

    # - "mưa": loại trừ động từ "mua" (mua sắm, mua bán, mua đồ, mua macbook, đi mua)
    if re.search(r"\bmưa\b", raw_lower):
        return True
    if re.search(r"\bmua\b", n) and not re.search(r"\b(?:mua\s+(?:sam|ban|do|macbook|dien thoai|hang|ao|quan|xe|nha)|di\s+mua)\b", n):
        if re.search(r"\b(?:troi|co|ngay|con|du|tran|sap)\s+mua\b|\bmua\s+(?:to|nho|phun|rao|da|bao|ngap)\b", n):
            return True

    # - "mát": loại trừ "mát-xa", "mát xa", "mất ngủ", "mất", "ánh mắt", "khuôn mặt", "mặt trời"
    if re.search(r"\bmát\s*[-–]?\s*xa\b", raw_lower) or re.search(r"\bmat\s*[-–]?\s*xa\b", n):
        pass
    elif re.search(r"\b(?:mát mẻ|trời mát|gió mát)\b", raw_lower):
        return True
    elif re.search(r"\bmát\b", raw_lower) and re.search(r"\b(?:trời|thời tiết|không khí|khí hậu|hôm nay|ngày mai|buổi|dễ chịu)\b", raw_lower):
        return True
    elif re.search(r"\b(?:mat me|troi mat)\b", n):
        return True

    # - "lạnh", "nóng":
    if re.search(r"\b(?:lạnh|lạnh giá|rét|nóng|nóng bức)\b", raw_lower):
        return True
    if re.search(r"\b(?:lanh|ret|nong)\b", n) and re.search(r"\b(?:troi|hom nay|ngay mai|thoi tiet|khi hau|rat|hoi)\s+(?:lanh|ret|nong)\b", n):
        return True

    # 4. Câu hỏi nối tiếp ngắn về ngày mai (vd: "ngày mai thế nào?", "còn ngày mai?") ngay sau lượt hỏi thời tiết
    if len(n.split()) <= 7 and re.search(
        r"\b(?:ngay\s+mai|tomorrow)\b.*\b(?:the\s+nao|ra\s+sao|sao\s+roi)\b|\b(?:con|vay|the)\s+ngay\s+mai\b",
        n,
    ):
        with _CHAT_HISTORY_LOCK:
            recent_hist = list(_CHAT_HISTORY[-8:])
        for item in recent_hist:
            content = str(item.get("content") or "")
            if "🌤️" in content or "thời tiết" in content.lower() or "dự báo" in content.lower():
                return True

    return False

# Ngữ cảnh đi trước các từ viết tắt tên tỉnh/thành phố 2 chữ cái để không thay nhầm từ thông thường (vd: "máy in hp", "nhắn tin nt")
_CITY_ABBR_PREFIX = r"\b(thời tiết|thoi tiet|nhiệt độ|nhiet do|dự báo|du bao|mưa|nắng|giờ|ở|o|tại|tai|đi|di|vào|vao|ra|từ|tu)\s+"

# Từ điển chuẩn hóa từ viết tắt, teencode, gõ không dấu và lỗi nhận diện giọng nói (STT) phổ biến
_VN_SHORTHAND_MAP: list[tuple[str, str]] = [
    # Sửa lỗi STT tiếng Việt hay nghe nhầm tên "Wisp"
    (r"\b(chào|ê|này|ơi|hỏi|bạn)\s+(quý|whip|wish|huyết|quýt|vip|bíp|quất)\b", r"\1 Wisp"),
    (r"^(quý|whip|wish|huyết|quýt)\s+(ơi|à|nhé|cho)\b", r"Wisp \2"),
    # Địa danh viết tắt phổ biến (an toàn theo ngữ cảnh)
    (r"\b(tp\.?\s*hcm|tphcm|hcm|sg)\b", "Sài Gòn"),
    (r"\bbmt\b", "Buôn Ma Thuột"),
    (_CITY_ABBR_PREFIX + r"hn\b", r"\1 Hà Nội"),
    (_CITY_ABBR_PREFIX + r"(?:đn|dn)\b", r"\1 Đà Nẵng"),
    (_CITY_ABBR_PREFIX + r"(?:đl|dl)\b", r"\1 Đà Lạt"),
    (_CITY_ABBR_PREFIX + r"hp\b", r"\1 Hải Phòng"),
    (_CITY_ABBR_PREFIX + r"ct\b", r"\1 Cần Thơ"),
    (_CITY_ABBR_PREFIX + r"nt\b", r"\1 Nha Trang"),
    (_CITY_ABBR_PREFIX + r"vt\b", r"\1 Vũng Tàu"),
    # Từ viết tắt tiếng Việt thông dụng (chỉ thay các từ teencode rõ ràng, không đụng vào chữ 'k', 'hông', 'bn')
    (r"\b(ko|khg|hok)\b", "không"),
    (r"\b(dc|đc)\b", "được"),
    (r"\bntn\b", "như thế nào"),
    (r"\bbnhiu\b", "bao nhiêu"),
    (r"\b(bh|bjo)\b", "bây giờ"),
    (r"\bhnay\b", "hôm nay"),
    (r"\bnmai\b", "ngày mai"),
    (r"\b(mk|mik)\b", "mình"),
]

# Khôi phục dấu tiếng Việt cho các cụm từ khóa thông dụng khi gõ không dấu
_UNACCENTED_PHRASE_MAP: list[tuple[str, str]] = [
    (r"\bthoi tiet\b", "thời tiết"),
    (r"\bnhiet do\b", "nhiệt độ"),
    (r"\bmay gio roi\b", "mấy giờ rồi"),
    (r"\bmay gio\b", "mấy giờ"),
    (r"\bbay gio\b", "bây giờ"),
    (r"\bhien tai\b", "hiện tại"),
    (r"\bhom nay\b", "hôm nay"),
    (r"\bngay mai\b", "ngày mai"),
    (r"\bngay may\b", "ngày mấy"),
    (r"\bthu may\b", "thứ mấy"),
    (r"\bxoa lich su\b", "xóa lịch sử"),
    (r"\bxin chao\b", "xin chào"),
    (r"\bcam on\b", "cảm ơn"),
    (r"\btam biet\b", "tạm biệt"),
    (r"\bsai gon\b", "Sài Gòn"),
    (r"\bho chi minh\b", "Hồ Chí Minh"),
    (r"\bha noi\b", "Hà Nội"),
    (r"\bda nang\b", "Đà Nẵng"),
    (r"\bda lat\b", "Đà Lạt"),
    (r"\bhai phong\b", "Hải Phòng"),
    (r"\bcan tho\b", "Cần Thơ"),
    (r"\bvung tau\b", "Vũng Tàu"),
    (r"\bnha trang\b", "Nha Trang"),
    (r"\bquy nhon\b", "Quy Nhơn"),
    (r"\bphan thiet\b", "Phan Thiết"),
    (r"\bbuon ma thuot\b", "Buôn Ma Thuột"),
]


_CODE_SEGMENT_RE = re.compile(r"(`{3,})[\s\S]*?(?:\1`*|$)|``(?:(?!``)[^\n])+``|`[^`\n]+`")
_LIKELY_CODE_LINE_RE = re.compile(
    r"^\s*(?:let|var|const|func|function|def|class|struct|enum|extension|protocol|interface|type|typedef|"
    r"import|export|return|yield|elif|else|switch|case|guard|async|await|try|catch|finally|throw|throws|"
    r"public|private|protected|internal|fileprivate|static|final|override|mutating|void|int|double|float|bool|"
    r"String|Widget|State|BuildContext|@[A-Za-z_]\w*|//|/\*|\*/|\*\s|#include|#define|#pragma)\b"
    r"|^\s*(?:if|for|while)\b(?=.*(?:[({:=<>!]|->|=>))"
    r"|^\s*[\"'][A-Za-z0-9_.-]+[\"']\s*:"
    r"|^\s*</?[A-Za-z][A-Za-z0-9_.-]*(?:\s|>|/>)"
    r"|[{}]|=>|->|===|!==|==|!=|<=|>=|\+=|-=|\*=|/=|:=|\s(?:&&|\|\|)\s"
    r"|;\s*(?://|#|$)"
    r"|\b[A-Za-z_][A-Za-z0-9_.]*\s*=\s*[^=]"
    r"|\b[A-Za-z_]\w*\.[A-Za-z_]\w*\("
    r"|^\s*[A-Za-z_][A-Za-z0-9_.]*\([^()\n]*\)\s*;?\s*$"
)
_LIKELY_SQL_OR_CLI_RE = re.compile(
    r"^\s*(?:SELECT\s+.+\s+FROM|INSERT\s+INTO|UPDATE\s+\S+\s+SET|DELETE\s+FROM|"
    r"CREATE\s+(?:TABLE|INDEX|VIEW|DATABASE)|ALTER\s+TABLE|DROP\s+(?:TABLE|INDEX|VIEW))\b"
    r"|^\s*(?:git\s+(?:init|clone|status|add|commit|push|pull|fetch|checkout|switch|branch|merge|rebase|reset|stash|log|diff|tag|remote|cherry-pick)"
    r"|npm\s+(?:install|i|run|start|test|build|init|ci|uninstall|publish|exec)"
    r"|(?:npx|yarn|pnpm)\s+[A-Za-z0-9_./@-]+"
    r"|(?:pip|pip3)\s+(?:install|uninstall|freeze|list|show)"
    r"|(?:python|python3)\s+(?:-[mc]\b|\S+\.py\b)"
    r"|pytest(?:\s|$)"
    r"|flutter\s+(?:run|build|pub|clean|create|test|doctor|analyze|upgrade|channel)"
    r"|dart\s+(?:run|compile|pub|analyze|test|fix|format)"
    r"|swift\s+(?:build|run|test|package)"
    r"|docker\s+(?:run|build|compose|ps|exec|pull|push|images|logs|stop|rm)"
    r"|(?:cd|mkdir|rmdir|chmod|chown|curl|wget)\s+[A-Za-z0-9_./~$-])\b",
    flags=re.IGNORECASE,
)


def _iter_code_and_prose_segments(s: str) -> list[tuple[bool, str]]:
    segments: list[tuple[bool, str]] = []
    last = 0
    for m in _CODE_SEGMENT_RE.finditer(s):
        if m.start() > last:
            segments.append((False, s[last : m.start()]))
        segments.append((True, m.group(0)))
        last = m.end()
    if last < len(s):
        segments.append((False, s[last:]))
    return segments


def _is_likely_code_line(line: str, is_multiline: bool = False) -> bool:
    if not line or not line.strip():
        return False
    if is_multiline and re.match(r"^[\t ]{2,}\S", line):
        return True
    if _LIKELY_CODE_LINE_RE.search(line):
        return True
    if not line.rstrip().endswith("?") and _LIKELY_SQL_OR_CLI_RE.search(line):
        return True
    return False


def _normalize_prose_fragment(frag: str) -> str:
    if not frag:
        return ""
    s = re.sub(r"[ \t]+", " ", frag)
    if not s.strip():
        return " " if " " in s else ""
    # Loại bỏ cụm >= 2 từ bị lặp liên tiếp do ranh giới STT interim -> final, giữ nguyên từ láy đơn
    s = re.sub(r"\b(\w+\s+\w+)\s+\1\b", r"\1", s, flags=re.IGNORECASE)
    for pattern, replacement in _UNACCENTED_PHRASE_MAP:
        s = re.sub(pattern, replacement, s, flags=re.IGNORECASE)
    for pattern, replacement in _VN_SHORTHAND_MAP:
        s = re.sub(pattern, replacement, s, flags=re.IGNORECASE)
    return re.sub(r"[ \t]+", " ", s)


def normalize_vietnamese_input(raw_text: str) -> str:
    """
    Chuẩn hóa đầu vào tiếng Việt (từ cả giọng nói STT và bàn phím):
    1. Chuẩn hóa bảng mã Unicode dựng sẵn (NFC) và ký tự Đ/đ.
    2. Bảo toàn nguyên vẹn xuống dòng (\\n), thụt lề (indentation) và tên biến trong khối code
       (```...```, ````...````, `...`, hoặc dòng mã nguồn / SQL / CLI paste trực tiếp như `let sg = 1; let dc = 2`).
    3. Với văn xuôi tiếng Việt: làm sạch khoảng trắng thừa, khử lặp từ STT, mở rộng từ viết tắt
       và khôi phục dấu tiếng Việt cho các cụm từ khóa thông dụng.
    """
    if not raw_text:
        return ""
    s = unicodedata.normalize("NFC", str(raw_text))
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("Ð", "Đ").replace("ð", "đ")
    s = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", s)
    s = s.replace("\u00a0", " ")
    if not s.strip():
        return ""

    # Tách bỏ các dòng trống thừa ở đầu/cuối nhưng giữ nguyên thụt lề của dòng code đầu tiên
    s = re.sub(r"^(?:[ \t]*\n)+", "", s)
    s = s.rstrip()
    is_multiline = "\n" in s

    segments = _iter_code_and_prose_segments(s)
    out_parts: list[str] = []
    for idx, (is_code, part) in enumerate(segments):
        if not part:
            continue
        if is_code:
            # Đoạn nằm trong ```...``` / ````...```` hoặc `...` -> giữ nguyên 100% từng ký tự và khoảng trắng
            out_parts.append(part)
            continue

        lines = part.split("\n")
        norm_lines: list[str] = []
        for line_idx, line in enumerate(lines):
            if _is_likely_code_line(line, is_multiline=is_multiline):
                norm_lines.append(line.rstrip())
            else:
                norm_frag = _normalize_prose_fragment(line)
                # Chỉ trim đầu dòng nếu thực sự ở đầu dòng (không liền sau inline `code`)
                at_line_start = (idx == 0) or (line_idx > 0)
                # Chỉ trim cuối dòng nếu thực sự ở cuối dòng (không liền trước inline `code`)
                at_line_end = (idx == len(segments) - 1) or (line_idx < len(lines) - 1)
                if at_line_start:
                    norm_frag = norm_frag.lstrip(" \t")
                if at_line_end:
                    norm_frag = norm_frag.rstrip(" \t")
                norm_lines.append(norm_frag)
        out_parts.append("\n".join(norm_lines))

    result = "".join(out_parts)
    if not is_multiline and "```" not in result:
        return result.strip()
    return result



def _strip_accents(s: str) -> str:
    """'thời tiết' -> 'thoi tiet' để so khớp không cần gõ dấu."""
    s = s.replace("Đ", "D").replace("đ", "d").replace("Ð", "D").replace("ð", "d")
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def _norm(s: str) -> str:
    return _strip_accents(s.lower()).strip()


def _strip_conversational_fillers(text_norm: str) -> str:
    """Loại bỏ các từ đệm giao tiếp tự nhiên trong tiếng Việt (khi nói hoặc gõ) để bắt đúng lệnh cốt lõi."""
    t = text_norm.strip(" ,.?!;:\"'“”‘’")
    # Bỏ cụm mở đầu xã giao
    t = re.sub(
        r"^(?:e\s+|ey\s+|alo\s+|chao\s+|xin\s+chao\s+)?(?:wisp\s+)?(?:oi\s+)?"
        r"(?:cho\s+(?:minh|em|anh|to|tui)\s+(?:hoi|xem|biet)\s+|cho\s+(?:hoi|xem|biet)\s+|hoi\s+)?",
        "",
        t,
    )
    # Bỏ từ cảm thán cuối câu
    t = re.sub(
        r"\s+(?:nhi|vay|the|nhe|nha|di|a|ha|coi|voi|ban\s+oi|wisp\s+oi|wisp)+$",
        "",
        t,
    )
    return re.sub(r"\s+", " ", t).strip(" ,.?!;:")


def _extract_city_after(text_norm: str, keywords: list[str]) -> str:
    """Lấy phần thành phố trong câu hỏi thời tiết, ưu tiên khớp chính xác CITY_ALIASES."""
    norm = _norm(text_norm)
    # 1. Ưu tiên khớp trực tiếp tên thành phố đã biết trong toàn câu (vd: "ở Đà Lạt thời tiết ngày mai thế nào")
    for alias in sorted(CITY_ALIASES.keys(), key=len, reverse=True):
        if re.search(rf"\b{re.escape(alias)}\b", norm):
            return alias

    # 2. Nếu là thành phố khác không nằm trong alias có sẵn, tách phần sau từ khóa
    t = norm
    for kw in keywords:
        kw_n = _norm(kw)
        if kw_n in t:
            t = t.split(kw_n, 1)[1]
            break
    # Bỏ các từ thừa, từ đệm câu hỏi và các cụm từ tư vấn không thuộc về địa danh
    t = re.sub(
        r"\b(o|tai|thanh pho|tinh|hien tai|bay gio|hom nay|ngay mai|tomorrow|"
        r"the nao|nhu the nao|ra sao|sao roi|bao nhieu do|may do|nhiet do bao nhieu|"
        r"mac gi|an gi|di dau|choi gi|outfit|mang gi|mang o|ao mua|ao khoac|co mua khong|co nang khong|"
        r"nhi|vay|the|nhe|nha|di|a|ha|coi|voi|wisp|oi)\b",
        " ",
        t,
    )
    t = re.sub(r"\s+", " ", t).strip(" ,.?!;:\"'“”")
    return t


# Lưu trữ Hồ sơ Cá nhân của chủ nhân (Persistent Assistant Memory)
USER_PROFILE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_profile.json")

DEFAULT_USER_PROFILE: dict = {
    "name": "Bạn",
    "gender": "Nam",
    "age": "",
    "height_cm": "",
    "weight_kg": "",
    "city": "TP. Hồ Chí Minh",
    "job": "iOS/SwiftUI & Flutter Developer",
    "notes": "",
    "auto_learn": True,
}

_USER_PROFILE_CACHE: dict | None = None

_CITY_DISPLAY_MAP: dict[str, str] = {
    "sai gon": "TP. Hồ Chí Minh",
    "ho chi minh": "TP. Hồ Chí Minh",
    "tp ho chi minh": "TP. Hồ Chí Minh",
    "tp. ho chi minh": "TP. Hồ Chí Minh",
    "tphcm": "TP. Hồ Chí Minh",
    "hcm": "TP. Hồ Chí Minh",
    "ha noi": "Hà Nội",
    "hanoi": "Hà Nội",
    "da nang": "Đà Nẵng",
    "da lat": "Đà Lạt",
    "hai phong": "Hải Phòng",
    "can tho": "Cần Thơ",
    "nha trang": "Nha Trang",
    "vung tau": "Vũng Tàu",
    "hue": "Huế",
    "quy nhon": "Quy Nhơn",
    "buon ma thuot": "Buôn Ma Thuột",
    "phan thiet": "Phan Thiết",
    "phu quoc": "Phú Quốc",
    "bien hoa": "Biên Hòa",
    "vinh": "Vinh",
    "ha long": "Hạ Long",
}


def _normalize_profile_dict(raw_prof: dict | None = None) -> dict:
    merged = dict(DEFAULT_USER_PROFILE)
    if not isinstance(raw_prof, dict):
        return merged

    for str_key in ("name", "gender", "age", "height_cm", "weight_kg", "city", "job", "notes"):
        if str_key in raw_prof and raw_prof[str_key] is not None:
            val = str(raw_prof[str_key]).strip()
            merged[str_key] = val

    if not merged["name"]:
        merged["name"] = "Bạn"
    if not merged["gender"]:
        merged["gender"] = "Nam"
    if not merged["city"]:
        merged["city"] = "TP. Hồ Chí Minh"

    if "auto_learn" in raw_prof:
        merged["auto_learn"] = bool(raw_prof["auto_learn"])

    return merged


def compute_profile_metrics(profile: dict) -> dict:
    """Tính toán chỉ số BMI, phân loại thể trạng và nhu cầu nước uống từ chiều cao & cân nặng."""
    metrics: dict = {"bmi": None, "bmi_label": "", "water_liters": None}
    try:
        h_raw = str(profile.get("height_cm") or "").replace(",", ".").strip()
        w_raw = str(profile.get("weight_kg") or "").replace(",", ".").strip()
        h = float(h_raw) if h_raw else 0.0
        w = float(w_raw) if w_raw else 0.0
        if 100.0 <= h <= 230.0 and 25.0 <= w <= 220.0:
            bmi = round(w / ((h / 100.0) ** 2), 1)
            if bmi < 18.5:
                label = "Thiếu cân"
            elif bmi < 23.0:
                label = "Cân đối"
            elif bmi < 25.0:
                label = "Hơi thừa cân"
            else:
                label = "Thừa cân"
            metrics["bmi"] = bmi
            metrics["bmi_label"] = label
            metrics["water_liters"] = round(w * 0.035, 1)
    except (ValueError, TypeError):
        pass
    return metrics


def get_user_profile() -> dict:
    """Đọc Hồ sơ cá nhân từ RAM hoặc file user_profile.json (kèm chỉ số BMI)."""
    global _USER_PROFILE_CACHE
    if _USER_PROFILE_CACHE is None:
        loaded = {}
        if os.path.exists(USER_PROFILE_FILE):
            try:
                with open(USER_PROFILE_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
            except Exception:
                loaded = {}
        _USER_PROFILE_CACHE = _normalize_profile_dict(loaded)
    result = dict(_USER_PROFILE_CACHE)
    result.update(compute_profile_metrics(result))
    return result


def update_user_profile(patch: dict | None = None) -> dict:
    """Cập nhật và lưu bền vững Hồ sơ cá nhân vào user_profile.json."""
    global _USER_PROFILE_CACHE
    current = get_user_profile()
    if isinstance(patch, dict):
        for k in ("name", "gender", "age", "height_cm", "weight_kg", "city", "job", "notes", "auto_learn"):
            if k in patch:
                current[k] = patch[k]
    _USER_PROFILE_CACHE = _normalize_profile_dict(current)
    try:
        with open(USER_PROFILE_FILE, "w", encoding="utf-8") as f:
            json.dump(_USER_PROFILE_CACHE, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    result = dict(_USER_PROFILE_CACHE)
    result.update(compute_profile_metrics(result))
    return result


def reset_user_profile() -> dict:
    """Khôi phục Hồ sơ cá nhân về mặc định."""
    global _USER_PROFILE_CACHE
    _USER_PROFILE_CACHE = dict(DEFAULT_USER_PROFILE)
    try:
        with open(USER_PROFILE_FILE, "w", encoding="utf-8") as f:
            json.dump(_USER_PROFILE_CACHE, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    result = dict(_USER_PROFILE_CACHE)
    result.update(compute_profile_metrics(result))
    return result


def _get_user_default_city() -> str:
    """Lấy thành phố mặc định từ Hồ sơ cá nhân của người dùng để tra cứu thời tiết."""
    prof = get_user_profile()
    city_str = str(prof.get("city") or "").strip()
    if not city_str:
        return "Ho Chi Minh"
    city_norm = _norm(city_str)
    for alias in sorted(CITY_ALIASES.keys(), key=len, reverse=True):
        if alias in city_norm:
            return alias
    return city_str


# Danh mục toàn diện các danh từ thân tộc, xã hội, vật nuôi và tài sản trong tiếng Việt
_RELATIVE_NOUNS = (
    r"(?:"
    r"bạn(?:\s+(?:thân|bè|gái|trai|cùng\s+phòng|cùng\s+lớp))?|"
    r"người\s+yêu(?:\s+cũ)?|bồ|crush|"
    r"vợ|chồng|bà\s+xã|ông\s+xã|hôn\s+phu|hôn\s+thê|"
    r"(?:bố|ba|cha)\s+mẹ|"
    r"(?:bố|ba|cha|tía|mẹ|má|u)(?:\s+(?:chồng|vợ|đẻ|ruột|nuôi))?|"
    r"(?:anh|chị|em)(?:\s+(?:ruột|họ|dâu|rể|trai|gái))?|"
    r"(?:con|cháu|chắt|bé)(?:\s+(?:ruột|họ|dâu|rể|trai|gái|nhỏ|cưng))?|"
    r"(?:ông|bà)(?:\s+(?:nội|ngoại|cố))?|cụ|cố|"
    r"bác|chú|cô|dì|thím|cậu|mợ|dượng|"
    r"sếp|đồng\s+nghiệp|cấp\s+(?:trên|dưới)|nhân\s+viên|"
    r"thầy(?:\s+giáo)?|cô(?:\s+giáo)?|học\s+sinh|sinh\s+viên|"
    r"chó|mèo|cún|thú\s+cưng|pet|"
    r"xe(?:\s+(?:máy|hơi|đạp))?|ô\s*tô|oto|vali|hành\s+lý|kiện\s+hàng|gói\s+hàng|cây(?:\s+(?:mai|đào|cảnh))?"
    r")"
)


def _is_preceded_by_possessive(text: str, start_pos: int) -> bool:
    """
    Kiểm tra xem vị trí bắt đầu của đại từ ngôi thứ nhất có bị chi phối bởi quan hệ sở hữu / người thứ 3:
    1. Lời chào xã giao (vd: 'Chào bạn mình 26 tuổi'): không chặn.
    2. Cấu trúc '... của' (vd: 'Bố của mình', 'Chó của mình'): chặn toàn bộ, trừ danh từ chỉ thuộc tính người dùng
       (chiều cao, cân nặng, năm sinh, tuổi, tên).
    3. Cấu trúc sở hữu đẳng lập và thân tộc mở rộng (vd: 'Con gái mình', 'Bạn thân mình', 'Bé nhà mình', 'Xe mình').
    """
    prefix = text[:start_pos].rstrip()
    if not prefix:
        return False

    # 1. Bỏ qua lời chào xã giao hướng tới listener khi người dùng không gõ dấu phẩy
    if re.search(r"\b(?:chào|chao|hi|hello|alo|ê|hey)\s+(?:bạn|anh|em|bot)$", prefix, flags=re.IGNORECASE):
        return False

    # 2. Xử lý giới từ sở hữu 'của'
    if re.search(r"\bcủa$", prefix, flags=re.IGNORECASE):
        # Cho phép nếu là thuộc tính chính chủ của người dùng
        if re.search(
            r"\b(?:chiều\s+cao|cân\s+nặng|năm\s+sinh|tuổi|tên)"
            r"(?:\s+(?:hiện\s+tại|chính\s+xác|thực\s+tế))?\s+của$",
            prefix,
            flags=re.IGNORECASE,
        ):
            return False
        return True

    # 3. Xử lý danh từ thân tộc, quan hệ xã hội, vật nuôi, tài sản (hỗ trợ cả biến thể '... nhà')
    rel_pattern = rf"\b{_RELATIVE_NOUNS}(?:\s+nhà)?$"
    return bool(re.search(rel_pattern, prefix, flags=re.IGNORECASE))


def extract_and_update_profile_from_message(raw_message: str) -> dict[str, str]:
    """
    Tự động phát hiện và ghi nhớ thông tin cá nhân khi người dùng chia sẻ trong lúc trò chuyện
    (vd: 'mình 26 tuổi, cao 1m72, nặng 68kg, đang ở Đà Nẵng').
    Trả về dict các trường vừa được cập nhật mới.
    """
    if not raw_message:
        return {}
    prof = get_user_profile()
    if not prof.get("auto_learn", True):
        return {}

    msg = _CODE_SEGMENT_RE.sub(" ", raw_message).strip()
    if not msg:
        return {}
    updated: dict[str, str] = {}

    # 1. Tên gọi (1 đến 4 từ tiếng Việt, loại bỏ trợ từ cảm thán ở cuối như 'nha', 'nhé', 'nè', 'ạ')
    m_name = None
    for m in re.finditer(
        r"(?:tên\s+(?:của\s+)?(?:mình|tôi|anh|em)\s+là|gọi\s+(?:mình|tôi|anh|em)\s+là|(?:mình|tôi)\s+tên\s+là)\s+"
        r"([A-ZÀ-Ỹa-zà-ỹ]{2,15}(?:\s+[A-ZÀ-Ỹa-zà-ỹ]{2,15}){0,3})",
        msg,
        flags=re.IGNORECASE,
    ):
        if _is_preceded_by_possessive(msg, m.start()):
            continue
        m_name = m
        break

    if m_name:
        candidate_name = m_name.group(1).strip()
        candidate_name = re.sub(r"\s+\b(?:nha|nhé|nè|ạ|nhỉ|đó|đấy|nhá|hen|ơi)\b.*$", "", candidate_name, flags=re.IGNORECASE).strip().title()
        if candidate_name.lower() not in ("ai", "gì", "wisp", "bot", "lập trình viên") and len(candidate_name) >= 2:
            updated["name"] = candidate_name

    # 2. Tuổi hoặc Năm sinh (bắt buộc có đại từ xưng hô ngôi thứ nhất, bảo vệ đại từ ngôi thứ 3 và sở hữu)
    m_age = None
    for m in re.finditer(
        r"(?:(?:mình|tôi|(?:anh(?!\s+(?:ấy|ta|đó|họ)))|(?:em(?!\s+(?:ấy|ta|đó|họ))))(?:\s+năm\s+nay)?|"
        r"năm\s+nay\s+(?:mình|tôi|(?:anh(?!\s+(?:ấy|ta|đó|họ)))|(?:em(?!\s+(?:ấy|ta|đó|họ))))|"
        r"tuổi\s+của\s+(?:mình|tôi|(?:anh(?!\s+(?:ấy|ta|đó|họ)))|(?:em(?!\s+(?:ấy|ta|đó|họ))))(?:\s+năm\s+nay)?)\s+"
        r"(?:đã\s+|được\s+|vừa\s+|tròn\s+|khoảng\s+|là\s+|hiện\s+là\s+)?(?P<age>\d{1,2})\s*tuổi\b",
        msg,
        flags=re.IGNORECASE,
    ):
        if _is_preceded_by_possessive(msg, m.start()):
            continue
        m_age = m
        break

    if m_age:
        age_val = int(m_age.group("age"))
        if 10 <= age_val <= 95:
            updated["age"] = str(age_val)
    else:
        m_birth = None
        for m in re.finditer(
            r"(?:(?:mình|tôi|(?:anh(?!\s+(?:ấy|ta|đó|họ)))|(?:em(?!\s+(?:ấy|ta|đó|họ))))\s+(?:sinh\s+năm|sn)|"
            r"năm\s+sinh\s+của\s+(?:mình|tôi|(?:anh(?!\s+(?:ấy|ta|đó|họ)))|(?:em(?!\s+(?:ấy|ta|đó|họ)))))\s*(?:(?:là|=)\s+)?"
            r"(?P<byear>19\d{2}|20[01]\d)\b",
            msg,
            flags=re.IGNORECASE,
        ):
            if _is_preceded_by_possessive(msg, m.start()):
                continue
            m_birth = m
            break

        if m_birth:
            byear = int(m_birth.group("byear"))
            calc_age = datetime.now(VN_TZ).year - byear
            if 10 <= calc_age <= 95:
                updated["age"] = str(calc_age)

    # 3. Cân nặng (kg / ký) - Bắt buộc có chủ ngữ ngôi thứ nhất (loại bỏ đồ vật như 'Vali nặng 32kg' và người thân 'Bạn mình nặng 80kg')
    m_weight = None
    for m in re.finditer(
        r"(?:cân\s+nặng\s+của\s+(?:mình|tôi|(?:anh(?!\s+(?:ấy|ta|đó|họ)))|(?:em(?!\s+(?:ấy|ta|đó|họ))))(?:\s+là)?|"
        r"(?:mình|tôi|(?:anh(?!\s+(?:ấy|ta|đó|họ)))|(?:em(?!\s+(?:ấy|ta|đó|họ))))\s+(?:hiện\s+)?nặng)\s*"
        r"(?P<weight>\d{2,3}(?:[.,]\d)?)\s*(?:kg|ký|kí|kilogram)\b",
        msg,
        flags=re.IGNORECASE,
    ):
        if _is_preceded_by_possessive(msg, m.start()):
            continue
        m_weight = m
        break

    if m_weight:
        w_str = m_weight.group("weight").replace(",", ".")
        w_val = float(w_str)
        if 30.0 <= w_val <= 200.0:
            updated["weight_kg"] = str(int(w_val) if w_val.is_integer() else w_val)

    # 4. Chiều cao (1m72 hoặc 172cm) - Bắt buộc có chủ ngữ ngôi thứ nhất (loại bỏ 'Tòa nhà cao 180m' và người thân)
    m_height_m = None
    for m in re.finditer(
        r"(?:chiều\s+cao\s+của\s+(?:mình|tôi|(?:anh(?!\s+(?:ấy|ta|đó|họ)))|(?:em(?!\s+(?:ấy|ta|đó|họ))))(?:\s+là)?|"
        r"(?:mình|tôi|(?:anh(?!\s+(?:ấy|ta|đó|họ)))|(?:em(?!\s+(?:ấy|ta|đó|họ))))\s+(?:hiện\s+)?cao)\s*1\s*[mM]\s*(?P<cm>\d{1,2})\b",
        msg,
        flags=re.IGNORECASE,
    ):
        if _is_preceded_by_possessive(msg, m.start()):
            continue
        m_height_m = m
        break

    if m_height_m:
        cm_part = m_height_m.group("cm")
        h_cm = 100 + (int(cm_part) * 10 if len(cm_part) == 1 else int(cm_part))
        if 130 <= h_cm <= 220:
            updated["height_cm"] = str(h_cm)
    else:
        m_height_cm = None
        for m in re.finditer(
            r"(?:chiều\s+cao\s+của\s+(?:mình|tôi|(?:anh(?!\s+(?:ấy|ta|đó|họ)))|(?:em(?!\s+(?:ấy|ta|đó|họ))))(?:\s+là)?|"
            r"(?:mình|tôi|(?:anh(?!\s+(?:ấy|ta|đó|họ)))|(?:em(?!\s+(?:ấy|ta|đó|họ))))\s+(?:hiện\s+)?cao)\s*(?P<cm>1\d{2}|20\d)\s*(?:cm|centimet)?\b",
            msg,
            flags=re.IGNORECASE,
        ):
            if _is_preceded_by_possessive(msg, m.start()):
                continue
            m_height_cm = m
            break

        if m_height_cm:
            updated["height_cm"] = m_height_cm.group("cm")

    # 5. Nơi ở hiện tại (bỏ qua nếu là bối cảnh du lịch / công tác / tạm trú như 'Đang ở Đà Lạt du lịch')
    is_temporary_stay = bool(re.search(
        r"\b(?:du\s+lịch|du\s+lich|đi\s+chơi|di\s+choi|công\s+tác|cong\s+tac|nghỉ\s+dưỡng|nghi\s+duong|ghé\s+qua|ghe\s+qua|ghé\s+chơi|ghe\s+choi|thăm|tham)\b",
        msg,
        flags=re.IGNORECASE,
    ))
    if not is_temporary_stay:
        m_city = None
        for m in re.finditer(
            r"\b(?:(?:mình|tôi|(?:anh(?!\s+(?:ấy|ta|đó|họ)))|(?:em(?!\s+(?:ấy|ta|đó|họ)))|nhà\s+mình|hiện\s+tại|hiện)\s+(?:đang\s+)?(?:ở|sống\s+ở|sống\s+tại|làm\s+việc\s+ở|làm\s+việc\s+tại|chuyển\s+về|chuyển\s+ra|chuyển\s+vào)|"
            r"(?:đang\s+)?(?:sống\s+ở|sống\s+tại|làm\s+việc\s+ở|làm\s+việc\s+tại|định\s+cư\s+ở|chuyển\s+về|chuyển\s+ra|chuyển\s+vào))\s+"
            r"([A-ZÀ-Ỹa-zà-ỹ\s\.]{2,25}?)(?=[,.!?;]|\s+(?:và|nhưng|làm|nặng|cao|tuổi|được)|$)",
            msg,
            flags=re.IGNORECASE,
        ):
            if _is_preceded_by_possessive(msg, m.start()):
                continue
            m_city = m
            break

        if m_city:
            raw_city_Seg = _norm(m_city.group(1))
            for key, display_city in sorted(_CITY_DISPLAY_MAP.items(), key=lambda x: len(x[0]), reverse=True):
                if re.search(rf"\b{re.escape(key)}\b", raw_city_Seg):
                    updated["city"] = display_city
                    break

    # 6. Công việc / Chuyên môn
    m_job = re.search(
        r"\b(?:công\s+việc\s+của\s+(?:mình|tôi)\s+là|(?:mình|tôi)\s+làm\s+nghề|(?:mình|tôi)\s+đang\s+làm\s+nghề)\s+([^,.!?\n]{2,55})",
        msg,
        flags=re.IGNORECASE,
    )
    if m_job:
        updated["job"] = m_job.group(1).strip()

    # 7. Ghi chú / Thói quen / Lưu ý sức khỏe
    m_note = re.search(
        r"(?:ghi\s+nhớ\s+(?:giúp\s+mình|cho\s+mình|nhé)?\s*[:,-]?\s*)(.+)$",
        msg,
        flags=re.IGNORECASE,
    )
    if m_note:
        note_str = m_note.group(1).strip(" .!")
        if 3 <= len(note_str) <= 120:
            existing = str(prof.get("notes") or "").strip()
            if note_str.lower() not in existing.lower():
                updated["notes"] = f"{existing}; {note_str}".strip("; ") if existing else note_str

    if updated:
        # Chỉ lưu nếu thực sự có giá trị thay đổi so với hồ sơ hiện tại
        real_changes = {k: v for k, v in updated.items() if str(prof.get(k) or "") != str(v)}
        if real_changes:
            update_user_profile(real_changes)
            return real_changes
    return {}


def _build_user_profile_prompt() -> str:
    """Sinh khối thông tin Hồ sơ chủ nhân cho System Prompt của Trợ lý ảo Wisp."""
    prof = get_user_profile()
    lines: list[str] = ["\n[HỒ SƠ CÁ NHÂN CỦA CHỦ NHÂN (BỘ NHỚ TRỢ LÝ WISP)]:"]
    name = prof.get("name") or "bạn"
    lines.append(f"- Tên chủ nhân: {name}")

    if prof.get("gender"):
        lines.append(f"- Giới tính: {prof['gender']}")

    if prof.get("age"):
        lines.append(f"- Độ tuổi: {prof['age']} tuổi")

    h = prof.get("height_cm")
    w = prof.get("weight_kg")
    bmi = prof.get("bmi")
    bmi_label = prof.get("bmi_label")
    water = prof.get("water_liters")
    if h and w and bmi:
        lines.append(
            f"- Thể trạng & Chỉ số cơ thể: Cao {h} cm, nặng {w} kg "
            f"(BMI: {bmi} — {bmi_label}; lượng nước khuyến nghị: ~{water} lít/ngày)"
        )
    elif h or w:
        parts = []
        if h:
            parts.append(f"Cao {h} cm")
        if w:
            parts.append(f"Nặng {w} kg")
        lines.append(f"- Thể trạng: {', '.join(parts)}")

    if prof.get("city"):
        lines.append(f"- Nơi ở hiện tại: {prof['city']}")
    if prof.get("job"):
        lines.append(f"- Công việc / Chuyên môn: {prof['job']}")
    if prof.get("notes"):
        lines.append(f"- Sở thích / Thói quen / Lưu ý riêng: {prof['notes']}")

    lines.append(
        "- CÁCH SỬ DỤNG HỒ SƠ: Bạn là trợ lý cá nhân hiểu rõ chủ nhân của mình. "
        "Khi chủ nhân hỏi thông tin về bản thân (tên, tuổi, cân nặng, chiều cao, BMI, nơi ở, công việc) "
        "hoặc nhờ tư vấn ăn uống, dinh dưỡng, sức khỏe, vận động, trang phục, lịch trình hàng ngày, "
        "hãy chủ động kết hợp các chỉ số trong Hồ sơ trên để đưa ra lời khuyên "
        "cá nhân hóa sát thực tế nhất. Với các câu hỏi kiến thức chung không liên quan tới cá nhân, "
        "không tự ý liệt kê máy móc thông số cân nặng hay nghề nghiệp."
    )
    return "\n".join(lines)


def _build_system_prompt() -> str:
    user_name = get_user_profile().get("name") or "bạn"
    return (
        f"Bạn là Wisp, trợ lý AI cá nhân thông minh, tinh tế và nhạy bén chạy trên máy Windows của {user_name}.\n"
        "QUY TẮC CỐT LÕI:\n"
        "1. Luôn trả lời 100% bằng tiếng Việt chuẩn xác, tự nhiên, giàu tính thực tế (chỉ giữ tiếng Anh cho thuật ngữ lập trình, tên công nghệ hoặc tên riêng). TUYỆT ĐỐI KHÔNG dùng chữ Hán (tiếng Trung).\n"
        "2. Hiểu sâu ý định người dùng: phân tích đúng ngữ cảnh (thời gian thực, thời tiết thực tế nếu có, và lịch sử hội thoại trước đó) để đưa ra câu trả lời thông minh, hợp lý và hữu ích nhất.\n"
        f"3. Không tự ý nhắc tới nghề nghiệp hay kỹ năng lập trình của {user_name} trừ khi câu hỏi thực sự hỏi về lập trình.\n"
        "4. Khi viết tên lệnh, từ khóa kỹ thuật ngắn hoặc mã nguồn thì bọc trong dấu backtick."
    )


def _detect_city_in_text(text_norm: str) -> str:
    """Tìm tên thành phố trong câu hỏi tự nhiên (hoặc nhớ từ lịch sử hội thoại / hồ sơ cá nhân) để lấy thời tiết."""
    sorted_aliases = sorted(CITY_ALIASES.keys(), key=len, reverse=True)
    for alias in sorted_aliases:
        if re.search(rf"\b{re.escape(alias)}\b", text_norm):
            return alias
    if any(kw in text_norm for kw in ("thoi tiet", "weather", "nhiet do", "bao nhieu do", "may do")):
        extracted = _extract_city_after(text_norm, ["thoi tiet", "weather", "nhiet do", "bao nhieu do", "may do"])
        if extracted and len(extracted.split()) <= 4:
            return extracted
    # Nếu câu hiện tại là câu hỏi nối tiếp (vd: "vậy mặc gì hợp?"), kiểm tra thành phố vừa nhắc ở các lượt trước
    with _CHAT_HISTORY_LOCK:
        history_slice = list(_CHAT_HISTORY)
    # Ưu tiên kiểm tra câu hỏi người dùng trước để lấy chính xác địa danh người dùng đã nêu (thay vì tên tỉnh do geocoding trả về)
    for item in reversed(history_slice):
        if item.get("role") == "user":
            hist_norm = _norm(str(item.get("content") or ""))
            for alias in sorted_aliases:
                if re.search(rf"\b{re.escape(alias)}\b", hist_norm):
                    return alias
    for item in reversed(history_slice):
        hist_norm = _norm(str(item.get("content") or ""))
        for alias in sorted_aliases:
            if re.search(rf"\b{re.escape(alias)}\b", hist_norm):
                return alias
    return _get_user_default_city()


def _world_clock(query_norm: str) -> str | None:
    for key, (label, tz) in WORLD_TZ.items():
        if re.search(rf"\b{re.escape(key)}\b", query_norm):
            now = datetime.now(tz)
            return f"🕐 Bây giờ ở {label} là {now.strftime('%H:%M')} — {WEEKDAY_VI[now.weekday()]}, {now.strftime('%d/%m/%Y')}."
    return None


def _is_short_weather_command(text_norm: str) -> bool:
    """Kiểm tra xem câu có phải lệnh tra cứu thời tiết trực tiếp (kể cả khi nói có dấu ? hoặc từ đệm) hay không."""
    core = _strip_conversational_fillers(text_norm)
    if not any(kw in core for kw in ("thoi tiet", "weather", "nhiet do", "bao nhieu do", "may do")):
        return False
    # Loại trừ các câu hỏi tư vấn sâu cần LLM suy luận (mặc gì, ăn gì, đi chơi đâu, tại sao...)
    advisory_without_how = tuple(
        k for k in _ADVISORY_KEYWORDS if k not in ("the nao", "ra sao", "bao nhieu do", "may do")
    )
    if any(adv in core for adv in advisory_without_how):
        return False
    if len(core.split()) > 10:
        return False
    return True


def _match_fast_command(n: str) -> str | None:
    """Nhận diện các câu lệnh tra cứu nhanh (help, chào, giờ, ngày, cảm ơn, tạm biệt) sau khi đã lọc từ đệm."""
    norm_n = _norm(n)
    core = _strip_conversational_fillers(norm_n)
    n_clean = norm_n.strip(" ,.?!;:")
    user_name = get_user_profile().get("name") or "bạn"

    if core in ("help", "tro giup", "giup", "lenh", "menu", "?", "huong dan"):
        return HELP_TEXT
    if core in ("hi", "hello", "hey", "chao", "xin chao", "alo", "wisp", "chao wisp", "hello wisp"):
        return f"👋 Chào {user_name}! Mình là Wisp. Hỏi mình `thời tiết` hay `mấy giờ` đi."

    # Giờ quốc tế (vd: "giờ ở Tokyo", "bây giờ ở Nhật Bản là mấy giờ", "mấy giờ ở London")
    is_world_query = False
    if len(core.split()) <= 9 and not any(adv in core for adv in _ADVISORY_KEYWORDS):
        has_place_clock = bool(
            re.search(r"\b(?:may\s+gio|gio)\s+(?:o|tai)\s+([a-z\s]+)\b", core)
            or re.search(r"\b(?:o|tai)\s+([a-z\s]+)\s+(?:bay\s+gio\s+)?(?:may\s+gio|la\s+may\s+gio)\b", core)
        )
        has_known_key = any(re.search(rf"\b{re.escape(k)}\b", core) for k in WORLD_TZ)
        if (has_place_clock and has_known_key) or (("gio o" in core or "gio tai" in core) and has_known_key):
            is_world_query = True
        elif ("gio" in core or "may gio" in core) and any(re.search(rf"\b(?:o|tai)?\s*{re.escape(k)}\b", core) for k in (
            "nuoc anh", "nhat ban", "nuoc my", "hoa ky", "nuoc duc", "nuoc uc", "australia",
            "nuoc phap", "thai lan", "han quoc", "tokyo", "seoul", "bangkok", "singapore",
            "london", "paris", "berlin", "new york", "los angeles", "sydney", "dubai"
        )):
            is_world_query = True

    if is_world_query:
        world = _world_clock(core)
        if world:
            return world
        now = datetime.now(VN_TZ)
        return f"🕐 Bây giờ là {now.strftime('%H:%M:%S')} (giờ Việt Nam). Gõ `giờ ở Tokyo` để xem giờ thành phố khác nhé."

    # Giờ hiện tại Việt Nam
    if core in (
        "may gio", "may gio roi", "gio", "gio roi",
        "gio may gio", "time", "bay gio may gio", "bay gio la may gio",
        "bay gio la may gio roi", "hien tai la may gio", "hien tai may gio",
    ) or n_clean in ("may gio", "may gio roi", "gio", "time"):
        now = datetime.now(VN_TZ)
        return (
            f"🕐 Bây giờ là {now.strftime('%H:%M:%S')} — "
            f"{WEEKDAY_VI[now.weekday()]}, {now.strftime('%d/%m/%Y')} (giờ Việt Nam)."
        )

    # Ngày tháng hôm nay
    if core in (
        "hom nay", "ngay may", "ngay hom nay", "hom nay ngay may",
        "hom nay la ngay may", "thu may", "hom nay thu may",
        "hom nay la thu may", "hom nay thu may ngay bao nhieu",
        "hom nay la ngay bao nhieu", "ngay", "date", "today",
    ):
        now = datetime.now(VN_TZ)
        return f"📅 Hôm nay là {WEEKDAY_VI[now.weekday()]}, ngày {now.strftime('%d/%m/%Y')}."

    if core in ("cam on", "cam on nhe", "cam on ban", "cam on wisp", "thank", "thanks", "thank you"):
        return "Không có chi 😊 Cần gì cứ hỏi mình nhé."
    if core in ("tam biet", "tam biet wisp", "bye", "goodbye", "bai bai", "hen gap lai"):
        return f"👋 Bye {user_name}, hẹn gặp lại!"

    return None


def _resolve_ollama_model() -> str | None:
    """Kiểm tra model trong Ollama. Ưu tiên DEFAULT_MODEL, nếu chưa có thì lấy model đầu tiên có sẵn (có cache 120s)."""
    global _CACHED_OLLAMA_MODEL, _CACHED_OLLAMA_EXPIRES
    now_mono = time.monotonic()
    if _CACHED_OLLAMA_MODEL and now_mono < _CACHED_OLLAMA_EXPIRES:
        return _CACHED_OLLAMA_MODEL

    try:
        req = urllib.request.Request(OLLAMA_TAGS_URL, method="GET")
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        models = data.get("models") or []
        names = [m.get("name", "") for m in models if isinstance(m, dict) and m.get("name")]
        if not names:
            resolved = None
        else:
            resolved = names[0]
            for name in names:
                if name == DEFAULT_MODEL or name.startswith(f"{DEFAULT_MODEL}:"):
                    resolved = name
                    break
        if resolved:
            _CACHED_OLLAMA_MODEL = resolved
            _CACHED_OLLAMA_EXPIRES = now_mono + 120.0
            return resolved
    except Exception:
        if _CACHED_OLLAMA_MODEL:
            return _CACHED_OLLAMA_MODEL
        return DEFAULT_MODEL
    return _CACHED_OLLAMA_MODEL


DEFAULT_AI_PREFS: dict = {
    "style": "balanced",       # "concise" | "balanced" | "detailed"
    "persona": "minh_nguyen",  # "minh_nguyen" | "em_anh" | "direct"
    "no_greeting": True,
    "no_apology": True,
    "no_outro": True,
    "code_first": True,
    "plain_text": False,
}


def _normalize_ai_prefs(prefs: dict | None = None) -> dict:
    """Chuẩn hóa cấu hình phong cách trả lời của AI (luôn an toàn với None hoặc thiếu key)."""
    merged = dict(DEFAULT_AI_PREFS)
    if not isinstance(prefs, dict):
        return merged

    style = str(prefs.get("style") or merged["style"]).strip().lower()
    if style in ("concise", "balanced", "detailed"):
        merged["style"] = style

    persona = str(prefs.get("persona") or "minh_nguyen").strip().lower()
    if persona in ("minh_nguyen", "em_anh", "direct"):
        merged["persona"] = persona

    for bool_key in ("no_greeting", "no_apology", "no_outro", "code_first", "plain_text"):
        if bool_key in prefs:
            merged[bool_key] = bool(prefs[bool_key])

    return merged


_PROGRAMMING_KEYWORDS = (
    "code", "func", "class", "struct", "swift", "swiftui", "flutter",
    "python", "javascript", "typescript", "api", "thuật toán", "thuat toan",
    "snippet", "bug", "syntax", "cú pháp", "cu phap", "lập trình", "lap trinh",
)

_CODE_ACTION_KEYWORDS = (
    "viết hàm", "viet ham", "tạo hàm", "tao ham", "viết code", "viet code",
    "cách viết code", "cach viet code", "mẫu code", "mau code", "cách implement",
    "sửa lỗi code", "sua loi code", "sửa bug", "fix bug",
)

_CONCEPT_ONLY_PATTERNS = re.compile(
    r"\b(?:là\s+gì|la\s+gi|nghĩa\s+là\s+gì|khác\s+gì|so\s+sánh|so\s+sanh|ưu\s+nhược\s+điểm|làm\s+sao\s+để\s+giỏi)\b",
    re.IGNORECASE,
)


def _is_code_request(raw_message: str) -> bool:
    """
    Nhận diện chính xác câu hỏi thực sự yêu cầu viết code / cú pháp.
    Tránh kích hoạt nhầm Code-First cho câu hỏi khái niệm thuần túy hoặc câu hỏi thông thường
    (vd: 'SwiftUI là gì?', 'Gợi ý mẫu áo khoác', 'Viết lời chúc sinh nhật', 'Mình ham học').
    """
    if not raw_message:
        return False
    msg_lower = raw_message.lower()
    if _CONCEPT_ONLY_PATTERNS.search(msg_lower):
        return False

    for kw in _PROGRAMMING_KEYWORDS:
        if re.search(rf"\b{re.escape(kw)}\b", msg_lower):
            return True

    for act in _CODE_ACTION_KEYWORDS:
        if act in msg_lower:
            return True

    return False


def _build_style_rules_prompt(prefs: dict, is_code_query: bool = False, is_voice: bool = False) -> str:
    """Sinh chỉ dẫn phong cách trả lời thông minh cho System Prompt dựa trên tùy chọn của người dùng."""
    p = _normalize_ai_prefs(prefs)
    user_name = get_user_profile().get("name") or "bạn"
    rules: list[str] = ["\n[CẤU HÌNH PHONG CÁCH & QUY TẮC TRẢ LỜI]:"]

    # Quy tắc đặc thù cho Chế độ Đàm thoại Giọng nói (Voice Mode)
    if is_voice:
        rules.append(
            "- CHẾ ĐỘ ĐÀM THOẠI GIỌNG NÓI (VOICE MODE): "
            "Trả lời bằng khẩu ngữ tự nhiên, sống động, thân mật như đang trò chuyện 1-1 trực tiếp ngoài đời. "
            "TỐI ĐA 1–3 CÂU NGẮN GỌN (khoảng 20–50 từ), đi thẳng vào vấn đề cốt lõi. "
            "TUYỆT ĐỐI KHÔNG dùng dấu gạch đầu dòng (•, -, *), số thứ tự (1., 2.), tiêu đề (#), bảng biểu. "
            "TUYỆT ĐỐI KHÔNG đọc khối code dài ra loa: nếu câu hỏi về lập trình, chỉ tóm tắt giải pháp trong 1 câu và hướng dẫn xem mã chi tiết trên màn hình. "
            "Kết thúc câu mở tự nhiên, nhẹ nhàng để người dùng dễ tiếp lời."
        )

    # 1. Độ dài / Chế độ trả lời (linh hoạt theo độ phức tạp của câu hỏi)
    if is_voice:
        pass
    elif p["style"] == "concise":
        rules.append(
            "- CHẾ ĐỘ NGẮN GỌN (CONCISE & SMART): "
            "Trả lời súc tích, thông minh, đi thẳng ngay vào trọng tâm trong 1–3 câu gọn gàng (khoảng 15–55 từ tùy câu hỏi). "
            "Với câu hỏi tính toán hoặc sự thật đơn giản thì trả lời 1 câu trực tiếp; "
            "với câu hỏi tư vấn (ăn gì, mặc gì, đi đâu) hoặc giải thích kỹ thuật thì đưa ra gợi ý cụ thể, thực tế kèm lý do hợp lý ngắn gọn. "
            "Tuyệt đối không trả lời cụt lủn ngô nghê hay bỏ sót ý chính của câu hỏi."
        )
    elif p["style"] == "detailed":
        rules.append(
            "- CHẾ ĐỘ CHI TIẾT (DETAILED): Phân tích đầy đủ, chuyên sâu, giải thích rõ nguyên lý/bối cảnh, chia mục rõ ràng và đưa ví dụ thực tế cụ thể."
        )
    else:
        rules.append(
            "- CHẾ ĐỘ TỰ NHIÊN (BALANCED): Trả lời tự nhiên, thông minh, mạch lạc và có chiều sâu vừa phải (khoảng 2–5 câu, hoặc dùng gạch đầu dòng ngắn gọn khi liệt kê gợi ý)."
        )

    # 2. Cách xưng hô
    if p["persona"] == "em_anh":
        rules.append(
            f"- CÁCH XƯNG HÔ: Luôn xưng 'em' (chỉ Wisp) và gọi người dùng là 'anh {user_name}' (hoặc 'anh'), lịch sự và tự nhiên. "
            f"Phân biệt rõ chủ ngữ — tân ngữ: khi khuyên người dùng thì nói 'giúp anh {user_name}...', không viết nhầm thành 'giúp em...'."
        )
    elif p["persona"] == "direct":
        rules.append(
            "- CÁCH XƯNG HÔ TRỰC TIẾP: Trả lời khách quan, trực diện vào thông tin; không dùng đại từ xưng hô cá nhân "
            f"(mình, em, tôi, bạn, anh, {user_name}) và không dùng từ đệm cuối câu (nhé, nha, ạ)."
        )
    else:
        rules.append(
            f"- CÁCH XƯNG HÔ: Khi giao tiếp hoặc tư vấn thì xưng 'mình' (chỉ Wisp) và gọi người dùng là '{user_name}' một cách tự nhiên, thân thiện. "
            f"LƯU Ý QUAN TRỌNG: Phân biệt rõ chủ ngữ và tân ngữ — khi đưa lời khuyên cho người dùng thì đối tượng nhận lợi ích là '{user_name}' (ví dụ: 'giúp {user_name} ấm bụng hơn', 'để {user_name} thoải mái di chuyển'), TUYỆT ĐỐI KHÔNG viết nhầm thành 'giúp mình...'. "
            f"Với phép tính hoặc định nghĩa kiến thức khách quan thì trả lời trực tiếp, không gượng ép thêm 'thưa {user_name}' hay 'của {user_name}'."
        )

    # 3. Các quy tắc lọc câu rườm rà (No-Fluff)
    if p["no_greeting"]:
        rules.append(
            f"- KHÔNG CHÀO HỎI ĐẦU CÂU: Không mở đầu bằng 'Chào {user_name}', 'Chào anh', 'Xin chào', 'Dạ vâng', "
            "'Tất nhiên rồi', 'Chắc chắn rồi', 'Rất vui được hỗ trợ'. Đi thẳng ngay vào nội dung trả lời chính."
        )
    if p["no_apology"]:
        rules.append(
            "- KHÔNG XIN LỖI & RÀO ĐÓN: Không nói 'Xin lỗi', 'Là một trợ lý AI', 'Theo thông tin mình biết', 'Câu hỏi rất hay'."
        )
    if p["no_outro"]:
        rules.append(
            "- KHÔNG CÂU CHÚC & HỎI ĐUÔI CUỐI CÂU: Không kết thúc bằng 'Chúc bạn/anh...', "
            "'Bạn cần giúp gì thêm không?', 'Nếu có thắc mắc cứ hỏi nhé', 'Hy vọng thông tin này hữu ích'."
        )
    if is_code_query:
        if p["code_first"]:
            rules.append(
                "- CHẾ ĐỘ CODE-FIRST: Đưa khối code ```...``` lên đầu tiên, bỏ phần mở bài dài dòng. "
                "Sau khối code giải thích ngắn gọn điểm cốt lõi."
            )
        else:
            rules.append(
                "- TRÌNH BÀY CODE: Giải thích ngắn gọn ý chính trước rồi đưa khối code minh họa rõ ràng."
            )
    else:
        rules.append(
            "- KHÔNG CHÈN CODE THỪA: Câu hỏi không yêu cầu viết code thì không tự ý chèn khối code ```...```."
        )

    if p["plain_text"]:
        rules.append(
            "- CHẾ ĐỘ VĂN BẢN THUẦN (PLAIN TEXT): Không dùng emoji và không chia gạch đầu dòng (bullet •, -, *). "
            "Chỉ viết thành câu văn liền mạch."
        )

    return "\n".join(rules)


def _capitalize_first_char(s: str) -> str:
    s = s.lstrip()
    if not s:
        return ""
    if s[0].islower():
        return s[0].upper() + s[1:]
    return s


_LEADING_GREETING_PATTERNS = [
    re.compile(
        r"^(?:👋\s*)?(?:xin\s+chào|chào(?:\s+buổi\s+(?:sáng|trưa|chiều|tối))?|hello|hi|hey)\s*"
        r"(?:anh\s+nguyên|anh\s+nguyen|nguyên|nguyen|anh|bạn|em|wisp)?"
        r"(?:\s+(?:nhé|nha|ạ|ơi|thân\s+mến))?\s*[,!?.:;—\-]+\s*",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:(?:anh\s+nguyên|nguyên|anh|bạn)\s+ơi)\s*[,!?.:;—\-]+\s*",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:dạ\s+vâng\s+ạ|dạ\s+vâng|dạ\s+thưa\s+anh|dạ\s+anh\s+nguyên|dạ\s+anh|dạ\s+nguyên|dạ|vâng\s+ạ|vâng|"
        r"tất\s+nhiên\s+(?:là\s+được\s+)?rồi(?:\s+ạ)?|chắc\s+chắn\s+rồi(?:\s+ạ)?|được\s+chứ(?:\s+ạ)?|"
        r"ok\s*(?:anh\s+nguyên|anh|nguyên|bạn)?|tuyệt\s+vời(?:\s+quá)?|"
        r"rất\s+sẵn\s+lòng(?:\s+ạ)?|rất\s+vui\s+được\s+[^,.!?\n]{1,40})\s*[,!?.:;—\-]+\s*",
        re.IGNORECASE,
    ),
]

_LEADING_APOLOGY_PATTERNS = [
    re.compile(
        r"^(?:(?:mình|em|tôi)\s+)?(?:thành\s+thật\s+|rất\s+)?xin\s+lỗi[^,.!?\n]{0,60}[,!?.:;—\-]+\s*",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:là\s+một\s+(?:trợ\s+lý\s+|mô\s+hình\s+)?ai[^,.!?\n]{0,45}|với\s+tư\s+cách\s+là\s+[^,.!?\n]{1,45}|"
        r"theo\s+(?:như\s+)?(?:thông\s+tin\s+|dữ\s+liệu\s+)?(?:mình|em|tôi)\s+(?:biết|tìm\s+hiểu|ghi\s+nhận|cập\s+nhật)(?:\s+thì)?|"
        r"(?:đây\s+là\s+một\s+)?câu\s+hỏi\s+(?:của\s+(?:bạn|anh|nguyên)\s+)?rất\s+(?:hay|thú\s+vị|thực\s+tế)|"
        r"(?:về|đối\s+với)\s+(?:câu\s+hỏi|vấn\s+đề|yêu\s+cầu)\s+(?:này|của\s+(?:bạn|anh|nguyên)))\s*[,!?.:;—\-]+\s*",
        re.IGNORECASE,
    ),
]

_TRAILING_OUTRO_PATTERNS = [
    re.compile(
        r"(?:(?<=[.!?])\s+|\n+|,\s+)"
        r"(?:chúc\s+(?:anh\s+nguyên|nguyên|nguyen|anh|bạn|một\s+ngày)\s+[^.!?]{1,90}|"
        r"(?:anh\s+nguyên|nguyên|nguyen|anh|bạn)\s+(?:có\s+)?(?:cần|muốn)\s+(?:mình|em)?\s*(?:hỗ\s+trợ|giúp|tư\s+vấn|giải\s+thích|viết\s+thêm)\s+[^.!?]{1,85}|"
        r"nếu\s+(?:anh|bạn|nguyên|nguyen)?\s*(?:cần|có|muốn)\s+(?:thêm|bất\s+kỳ|hỏi|thắc\s+mắc)\s+[^.!?]{1,90}|"
        r"(?:cần\s+gì\s+thêm\s+|có\s+thắc\s+mắc\s+gì\s+)?(?:cứ|hãy)\s+(?:hỏi|bảo|nhắn|cho)\s+(?:mình|em)\s+[^.!?]{0,70}|"
        r"(?:hy\s+vọng|mong\s+rằng)\s+[^.!?]{1,90})"
        r"[.!?…\s]*$",
        re.IGNORECASE,
    ),
]


def _strip_leading_fluff(text: str, prefs: dict | None = None) -> str:
    """Cắt bỏ tức thì các cụm chào hỏi / rào đón / xin lỗi ở đầu câu trả lời."""
    if not text:
        return ""
    p = _normalize_ai_prefs(prefs)
    s = text.lstrip()

    for _ in range(4):
        changed = False
        if p["no_greeting"]:
            for pat in _LEADING_GREETING_PATTERNS:
                m = pat.match(s)
                if m and len(s) > m.end():
                    s = s[m.end() :].lstrip()
                    changed = True
        if p["no_apology"]:
            for pat in _LEADING_APOLOGY_PATTERNS:
                m = pat.match(s)
                if m and len(s) > m.end():
                    s = s[m.end() :].lstrip()
                    changed = True
        if not changed:
            break

    return _capitalize_first_char(s) if s != text.lstrip() else text


def _apply_plain_text_format(text: str) -> str:
    """Loại bỏ emoji, markdown bold/italic và chuyển danh sách bullet thành câu văn liền mạch khi bật plain_text."""
    if not text:
        return ""
    parts = re.split(r"(```[\s\S]*?```)", text)
    cleaned_parts: list[str] = []
    for idx, part in enumerate(parts):
        if idx % 2 == 1:
            cleaned_parts.append(part)
            continue
        s = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\ufe0e\ufe0f]", "", part)
        s = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", s)
        s = re.sub(r":\s*\n\s*(?:[•\-*]|\d+\.)\s*", ": ", s)
        s = re.sub(r"\n\s*(?:[•\-*]|\d+\.)\s*", ". ", s)
        s = re.sub(r"^\s*(?:[•\-*]|\d+\.)\s*", "", s)
        s = re.sub(r"\.\s*\.", ".", s)
        s = re.sub(r"[ \t]{2,}", " ", s)
        cleaned_parts.append(s)
    return "".join(cleaned_parts).strip()


# Regex nhận diện chữ "bạn" độc lập (không nằm trong từ ghép như "bạn bè", "tình bạn", "kết bạn", "người bạn", "bạn đọc", "bạn học", "bạn thân")
_STANDALONE_BAN_LOWER = re.compile(
    r"(?<![Tt]ình\s)(?<![Kk]ết\s)(?<![Nn]gười\s)(?<![Cc]ác\s)(?<![Nn]hững\s)(?<![Mm]ột\s)\bbạn\b"
    r"(?!\s+(?:bè|đọc|học|đời|thân|hữu|trai|gái|đồng\s+hành))"
)
_STANDALONE_BAN_UPPER = re.compile(
    r"(?<![Tt]ình\s)(?<![Kk]ết\s)(?<![Nn]gười\s)(?<![Cc]ác\s)(?<![Nn]hững\s)(?<![Mm]ột\s)\bBạn\b"
    r"(?!\s+(?:bè|đọc|học|đời|thân|hữu|trai|gái|đồng\s+hành))"
)


def _apply_persona_text_fixes(text: str, persona: str) -> str:
    """Chuẩn hóa đại từ xưng hô bên ngoài khối code một cách an toàn ngữ cảnh (không làm hỏng từ ghép hay đảo chủ ngữ)."""
    if not text:
        return ""
    user_name = get_user_profile().get("name") or "bạn"
    parts = re.split(r"(```[\s\S]*?```|`[^`\n]+`)", text)
    out: list[str] = []
    for idx, part in enumerate(parts):
        if idx % 2 == 1:
            out.append(part)
            continue
        s = part
        if persona == "em_anh":
            s = re.sub(r"\b(?:mình|tôi|tớ)\b", "em", s)
            s = re.sub(r"\b(?:Mình|Tôi|Tớ)\b", "Em", s)
            s = _STANDALONE_BAN_LOWER.sub("anh", s)
            s = _STANDALONE_BAN_UPPER.sub("Anh", s)
            if user_name and user_name.lower() != "bạn":
                s = re.sub(rf"(?<!\banh\s)(?<!\bAnh\s)\b{re.escape(user_name)}\b", f"anh {user_name}", s, flags=re.IGNORECASE)
                s = re.sub(
                    r"\bgiúp\s+em\s+(?=(?:cảm\s+thấy|di\s+chuyển|giữ\s+ấm|ấm\s+bụng|thoải\s+mái|không\s+lo|tránh\s+bị)\b)",
                    f"giúp anh {user_name} ",
                    s,
                    flags=re.IGNORECASE,
                )
                if s.startswith(f"anh {user_name}"):
                    s = f"Anh {user_name}" + s[len(f"anh {user_name}") :]
        elif persona == "direct":
            # Loại bỏ các cụm chủ ngữ cá nhân ở đầu câu để câu trả lời khách quan, trực diện
            s = re.sub(
                r"(?:^|(?<=[.!?]\s))(?:theo\s+(?:mình|em|tôi)\s*(?:thấy|nghĩ)?\s*[,:]?\s*)",
                "",
                s,
                flags=re.IGNORECASE,
            )
            s = re.sub(
                rf"(?:^|(?<=[.!?]\s))(?:mình|em|tôi)\s+(?:khuyên|gợi\s+ý|nghĩ|thấy|đề\s+xuất)\s+"
                rf"(?:anh\s+{re.escape(user_name)}|{re.escape(user_name)}|anh|bạn)\s+(?=(?:nên|hãy|có\s+thể|thử|cần)\b)",
                "",
                s,
                flags=re.IGNORECASE,
            )
            s = re.sub(
                rf"(?:^|(?<=[.!?]\s))(?:anh\s+{re.escape(user_name)}|{re.escape(user_name)}|anh|bạn)\s+(?=(?:nên|hãy|có\s+thể|thử|cần)\b)",
                "",
                s,
                flags=re.IGNORECASE,
            )
            s = re.sub(
                rf"(?:\s*,\s*|\s+)(?:đó\s+(?:anh\s+)?{re.escape(user_name)}|nhé\s+(?:anh\s+)?{re.escape(user_name)}|nha\s+(?:anh\s+)?{re.escape(user_name)}|"
                rf"nhé\s+anh|ạ\s+anh(?:\s+{re.escape(user_name)})?|nhé|nha|ạ|đấy|nhỉ)(?=[.!?,;:\s]|$)",
                "",
                s,
                flags=re.IGNORECASE,
            )
            s = _capitalize_first_char(s)
        else:
            # minh_ban / minh_nguyen
            s = re.sub(r"\b(?:tôi|tớ)\b", "mình", s)
            s = re.sub(r"\b(?:Tôi|Tớ)\b", "Mình", s)
            # Giữ nguyên chữ 'bạn' tự nhiên trong tiếng Việt, không thay thế máy móc chữ 'bạn'
        out.append(s)
    return "".join(out)


def _shorten_single_prose_block(prose: str, max_sentences: int = 3) -> str:
    """
    Làm gọn đoạn văn bản ở chế độ Ngắn gọn (`concise`) mà KHÔNG cắt cụt từ hay mệnh đề giữa câu:
    - Giữ tối đa `max_sentences` câu hoàn chỉnh (mặc định 3 câu), bảo toàn trọn vẹn lý do và ngữ nghĩa.
    """
    s = prose.strip()
    if not s:
        return ""

    # Tách theo ranh giới kết thúc câu thực sự (không cắt nhầm số thập phân như 25.8°C)
    sentences = [
        seg.strip()
        for seg in re.split(
            r"(?<=[.!?…])\s+(?=[A-ZÀÁẢÃẠĂẮẰẲẴẶÂẤẦẨẪẬĐÈÉẺẼẸÊẾỀỂỄỆÌÍỈĨỊÒÓỎÕỌÔỐỒỔỖỘƠỚỜỞỠỢÙÚỦŨỤƯỨỪỬỮỰỲÝỶỸỴ0-9\"'`•\-*])",
            s,
        )
        if seg.strip()
    ]
    if not sentences:
        return _capitalize_first_char(s)

    chosen = " ".join(sentences[:max_sentences]).strip()
    if chosen and chosen[-1] not in ".!?…":
        chosen += "."
    return _capitalize_first_char(chosen)


def _enforce_concise_reply(text: str, prefs: dict | None = None) -> str:
    """
    Định dạng gọn gàng phản hồi khi người dùng chọn chế độ Ngắn gọn (`style == 'concise'`):
    - Nếu có khối code ```...``` và bật `code_first`: đưa khối code lên đầu, kèm giải thích súc tích bên dưới.
    - Nếu không có khối code: giữ tối đa 3 câu hoàn chỉnh, tuyệt đối không cắt ngang câu.
    """
    if not text:
        return ""
    p = _normalize_ai_prefs(prefs)
    if p["style"] != "concise":
        return text

    parts = re.split(r"(```[\s\S]*?```)", text)
    if len(parts) == 1:
        return _shorten_single_prose_block(text, max_sentences=3)

    code_blocks = [parts[i].strip() for i in range(1, len(parts), 2) if parts[i].strip()]
    prose_before = parts[0].strip()
    prose_after = " ".join(parts[i].strip() for i in range(2, len(parts), 2) if parts[i].strip()).strip()

    first_code = code_blocks[0]
    if p["code_first"]:
        short_after = _shorten_single_prose_block(prose_after or prose_before, max_sentences=2)
        return f"{first_code}\n\n{short_after}".strip() if short_after else first_code
    else:
        short_before = _shorten_single_prose_block(prose_before or prose_after, max_sentences=2)
        return f"{short_before}\n\n{first_code}".strip() if short_before else first_code


def _format_concise_fast_path(text: str, p: dict) -> str:
    """
    Khi người dùng chủ động bật chế độ Ngắn gọn (`concise`), rút gọn cả các thẻ thời tiết / giờ giấc
    về 1 dòng súc tích nhưng vẫn giữ đầy đủ thông số cốt lõi.
    """
    s = text.strip()
    if p["style"] != "concise":
        return s

    # 1. Thẻ thời tiết hiện tại -> 1 dòng gọn
    m_cur = re.search(
        r"Thời tiết hiện tại tại\s+([^\n:]+):\s*\n"
        r"•\s*([^\n]+)\n"
        r"•\s*Nhiệt độ:\s*([\d.,]+)\s*°C\s*\(cảm giác như\s*([\d.,]+)\s*°C\)\s*\n"
        r"•\s*Độ ẩm:\s*([\d.,]+)%\s*\n"
        r"•\s*Gió:\s*([\d.,]+)\s*km/h",
        s,
    )
    if m_cur:
        place, desc, temp, feels, hum, wind = m_cur.groups()
        feels_part = f" (như {feels}°C)" if temp != feels else ""
        return f"🌤️ {place.strip()}: {temp}°C{feels_part}, {desc.strip()}, ẩm {hum}%, gió {wind} km/h."

    # 2. Thẻ dự báo ngày mai -> 1 dòng gọn
    m_tom = re.search(
        r"Dự báo ngày mai tại\s+([^\n:]+):\s*\n"
        r"•\s*([^\n]+)\n"
        r"•\s*Nhiệt độ:\s*([\d.,]+)\s*°C\s*[–\-~]\s*([\d.,]+)\s*°C"
        r"(?:\s*\n•\s*Khả năng mưa:\s*([\d.,]+)%)?",
        s,
    )
    if m_tom:
        place, desc, tmin, tmax, rain = m_tom.groups()
        rain_part = f", mưa {rain}%" if rain else ""
        return f"🌤️ Ngày mai tại {place.strip()}: {tmin}°C – {tmax}°C, {desc.strip()}{rain_part}."

    # 3. Giờ hiện tại
    s = re.sub(
        r"^🕐\s*Bây giờ là\s+(\d{1,2}:\d{2})(?::\d{2})?\s*—\s*([^,]+),\s*(\d{1,2}/\d{1,2}/\d{4})\s*\(giờ Việt Nam\)\.$",
        r"🕐 \1 — \2, \3.",
        s,
    )
    # 4. Giờ quốc tế
    s = re.sub(
        r"^🕐\s*Bây giờ ở\s+([^\n]+?)\s+là\s+(\d{1,2}:\d{2})\s*—\s*([^,]+),\s*(\d{1,2}/\d{1,2}/\d{4})\.$",
        r"🕐 \1: \2 — \3, \4.",
        s,
    )
    # 5. Ngày hôm nay
    s = re.sub(
        r"^📅\s*Hôm nay là\s+([^,]+),\s*ngày\s+(\d{1,2}/\d{1,2}/\d{4})\.$",
        r"📅 \1, \2.",
        s,
    )
    # 6. Chào & Cảm ơn ngắn gọn
    s = re.sub(r"\s*Hỏi mình\s+`thời tiết`\s+hay\s+`mấy giờ`\s+đi\.", "", s)
    s = re.sub(r"\s*Cần gì cứ hỏi mình nhé\.", "", s)
    return s


def _clean_reply_by_prefs(text: str, prefs: dict | None = None, is_fast_path: bool = False) -> str:
    """Làm sạch câu trả lời hoàn chỉnh theo đúng cấu hình No-Fluff & Style của người dùng."""
    if not text:
        return ""
    if is_fast_path and prefs is None:
        return text.strip()

    p = _normalize_ai_prefs(prefs)
    s = text

    if is_fast_path:
        if p["no_outro"]:
            s = re.sub(r"\s*Gõ\s+`giờ ở Tokyo`\s+để xem giờ thành phố khác nhé\.", "", s)
            s = re.sub(r"\s*Cần gì cứ hỏi mình nhé\.", "", s)
            s = re.sub(r"\s*Hỏi mình\s+`thời tiết`\s+hay\s+`mấy giờ`\s+đi\.", "", s)
        s = _format_concise_fast_path(s, p)
        s = _apply_persona_text_fixes(s, p["persona"])
    else:
        # Với AI streaming: các câu đã được phát ra loa và hiển thị theo luồng token.
        # Độ dài được kiểm soát qua System Prompt; không chạy _enforce_concise_reply ở done
        # để không cắt giật làm mất các câu cuối đã xuất hiện trên màn hình.
        s = _apply_persona_text_fixes(s, p["persona"])

    if p["plain_text"]:
        s = _apply_plain_text_format(s)

    return s.strip() or text.strip()


def _filter_stream_tokens(token_iter, prefs: dict | None = None):
    """
    Bộ lọc thời gian thực ngay trên dòng chảy token:
    - Gom cụm mở đầu để cắt sạch lời chào / xin lỗi / rào đón trước khi phát token đầu tiên.
    - Giữ đệm ở ranh giới từ (word boundary) để chuẩn hóa xưng hô (`persona`) không bao giờ cắt đôi từ.
    - Khi đang trong code fence (in_code_fence == True): tắt hoàn toàn _transform_chunk, yield chunk nguyên bản.
    """
    p = _normalize_ai_prefs(prefs)
    need_lead_strip = bool(p["no_greeting"] or p["no_apology"])
    plain_mode = bool(p["plain_text"])

    opening_buf = ""
    lead_resolved = not need_lead_strip
    word_tail_buf = ""
    in_code_fence = False

    def _transform_chunk(chunk_str: str) -> str:
        res = _apply_persona_text_fixes(chunk_str, p["persona"])
        if plain_mode:
            res = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\ufe0e\ufe0f]", "", res)
            res = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", res)
            res = re.sub(r"\n\s*(?:[•\-*]|\d+\.)\s*", ". ", res)
        return res

    for delta in token_iter:
        if not delta:
            continue

        if not lead_resolved:
            opening_buf += delta
            if re.search(r"[,!?.:;\n]", opening_buf) or len(opening_buf) >= 56:
                cleaned_open = _strip_leading_fluff(opening_buf, p)
                if not cleaned_open.strip():
                    opening_buf = ""
                    continue
                if len(cleaned_open) < 12 and not re.search(r"[.!?\n]", cleaned_open):
                    opening_buf = cleaned_open
                    continue
                lead_resolved = True
                delta = cleaned_open
                opening_buf = ""
            else:
                continue

        combined = word_tail_buf + delta
        fence_count = combined.count("```")

        if in_code_fence:
            if fence_count > 0:
                # Closing fence arrived!
                code_part, text_part = combined.split("```", 1)
                yield code_part + "```"
                in_code_fence = False
                combined = text_part
            else:
                # Still inside code fence, yield as-is without _transform_chunk
                word_tail_buf = ""
                yield combined
                continue
        elif fence_count > 0:
            if fence_count % 2 == 1:
                # Entering code fence!
                text_part, code_part = combined.split("```", 1)
                out_chunk = _transform_chunk(text_part)
                word_tail_buf = ""
                if out_chunk:
                    yield out_chunk
                yield "```" + code_part
                in_code_fence = True
                continue

        if "```" in combined:
            out_chunk = _transform_chunk(combined)
            word_tail_buf = ""
            if out_chunk:
                yield out_chunk
            continue

        m_split = re.match(r"^([\s\S]*[\s,.!?;:\n—\-])([^\s,.!?;:\n—\-]*)$", combined)
        if m_split:
            ready_part, word_tail_buf = m_split.group(1), m_split.group(2)
        elif len(combined) >= 28:
            ready_part, word_tail_buf = combined, ""
        else:
            word_tail_buf = combined
            continue

        ready_part = _transform_chunk(ready_part)
        if ready_part:
            yield ready_part

    rem = opening_buf + word_tail_buf
    if rem.strip():
        if not lead_resolved:
            rem = _strip_leading_fluff(rem, p)
        if in_code_fence:
            yield rem
        else:
            rem = _transform_chunk(rem)
            if rem:
                yield rem


def _build_system_context(text_norm: str, prefs: dict | None = None, raw_message: str = "") -> str:
    """Tạo system prompt kèm Hồ sơ chủ nhân, cấu hình phong cách trả lời, thời gian thực và thời tiết thực tế."""
    now = datetime.now(VN_TZ)
    is_code = _is_code_request(raw_message or text_norm)
    ctx_lines = [
        _build_system_prompt(),
        _build_user_profile_prompt(),
        _build_style_rules_prompt(prefs or DEFAULT_AI_PREFS, is_code_query=is_code),
        f"\n[Ngữ cảnh thời gian thực]: Bây giờ là {now.strftime('%H:%M:%S')} — {WEEKDAY_VI[now.weekday()]}, ngày {now.strftime('%d/%m/%Y')} (giờ Việt Nam).",
    ]
    if _is_weather_topic(raw_message, text_norm):
        city = _detect_city_in_text(text_norm)
        is_tomorrow = "ngay mai" in text_norm or "tomorrow" in text_norm
        weather_info = get_tomorrow_weather(city) if is_tomorrow else get_current_weather(city)
        ctx_lines.append(
            f"[Dữ liệu thời tiết thực tế từ Open-Meteo]:\n{weather_info}\n"
            "(Hãy dùng chính xác số liệu và nêu rõ tên địa danh trong dữ liệu thời tiết trên để trả lời hoặc tư vấn phù hợp thực tế)."
        )
    return "\n".join(ctx_lines)


def _augment_user_prompt_with_prefs(raw_message: str, prefs: dict | None = None) -> str:
    """
    Giữ nguyên câu hỏi tự nhiên của người dùng (mọi quy tắc phong cách đã nằm trong System Prompt)
    để tránh làm nhiễu khả năng suy luận của LLM.
    """
    return raw_message.strip()


def _append_chat_history(user_msg: str, assistant_msg: str, is_fast_path: bool = False) -> None:
    max_msgs = MAX_HISTORY_TURNS * 2
    with _CHAT_HISTORY_LOCK:
        _CHAT_HISTORY.append({"role": "user", "content": user_msg})
        _CHAT_HISTORY.append({"role": "assistant", "content": assistant_msg, "is_fast_path": is_fast_path})
        if len(_CHAT_HISTORY) > max_msgs:
            del _CHAT_HISTORY[:-max_msgs]


def _clear_chat_history() -> None:
    global _GEMINI_NETWORK_OFFLINE_UNTIL
    with _CHAT_HISTORY_LOCK:
        _CHAT_HISTORY.clear()
    _GEMINI_COOLDOWN_UNTIL.clear()
    _GEMINI_NETWORK_OFFLINE_UNTIL = 0.0


def clear_chat_history() -> None:
    """Xóa sạch toàn bộ lịch sử hội thoại trong bộ nhớ backend (an toàn đa luồng)."""
    _clear_chat_history()


def get_chat_history() -> list[dict[str, str]]:
    """Lấy bản sao lịch sử hội thoại hiện tại (an toàn đa luồng)."""
    with _CHAT_HISTORY_LOCK:
        return [
            {
                "role": str(item.get("role") or "user"),
                "content": str(item.get("content") or ""),
            }
            for item in _CHAT_HISTORY
            if isinstance(item, dict) and str(item.get("content") or "").strip()
        ]


def restore_chat_history(items: list[dict], replace_only_if_empty: bool = True) -> int:
    """
    Khôi phục lịch sử hội thoại từ client (sau F5 / khởi động lại server) vào _CHAT_HISTORY.
    Mặc định chỉ nạp khi _CHAT_HISTORY đang rỗng để không ghi đè phiên đang hoạt động.
    """
    if not isinstance(items, list):
        return 0
    max_msgs = MAX_HISTORY_TURNS * 2
    cleaned: list[dict] = []
    for raw_item in items:
        if not isinstance(raw_item, dict):
            continue
        role = str(raw_item.get("role") or "").strip().lower()
        if role not in ("user", "assistant"):
            continue
        content = str(raw_item.get("content") or "").strip()
        if not content:
            continue
        entry: dict = {"role": role, "content": content}
        if role == "assistant":
            entry["is_fast_path"] = bool(raw_item.get("is_fast_path", False))
        cleaned.append(entry)

    if len(cleaned) > max_msgs:
        cleaned = cleaned[-max_msgs:]

    with _CHAT_HISTORY_LOCK:
        if replace_only_if_empty and _CHAT_HISTORY:
            return len(_CHAT_HISTORY)
        _CHAT_HISTORY.clear()
        _CHAT_HISTORY.extend(cleaned)
        return len(_CHAT_HISTORY)


def _get_sanitized_history_for_prefs(prefs: dict | None = None) -> list[dict[str, str]]:
    """
    Chuẩn hóa lại các câu trả lời cũ trong lịch sử theo đúng `prefs` hiện tại trước khi gửi cho LLM.
    """
    max_msgs = MAX_HISTORY_TURNS * 2
    sanitized: list[dict[str, str]] = []
    with _CHAT_HISTORY_LOCK:
        history_snapshot = list(_CHAT_HISTORY[-max_msgs:])
    for item in history_snapshot:
        role = item.get("role") or "user"
        text = str(item.get("content") or "").strip()
        if not text:
            continue
        if role == "assistant":
            is_fp = bool(item.get("is_fast_path", False))
            text = _clean_reply_by_prefs(text, prefs, is_fast_path=is_fp)
        sanitized.append({"role": role, "content": text})
    return sanitized


def _build_gemini_contents(raw_message: str, prefs: dict | None = None) -> list[dict]:
    """Chuyển đổi lịch sử _CHAT_HISTORY sang định dạng contents chuẩn của Google Gemini API."""
    contents: list[dict] = []
    for item in _get_sanitized_history_for_prefs(prefs):
        role = "model" if item.get("role") == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": item["content"]}]})
    contents.append({"role": "user", "parts": [{"text": _augment_user_prompt_with_prefs(raw_message, prefs)}]})
    return contents


def _build_gemini_generation_config(raw_message: str, prefs: dict | None = None) -> dict:
    p = _normalize_ai_prefs(prefs)
    return {
        "temperature": 0.35 if p["style"] == "concise" else 0.45,
        "thinkingConfig": {"thinkingBudget": 0},
    }


def _ask_gemini(raw_message: str, system_content: str, prefs: dict | None = None) -> str | None:
    """
    Gọi Google AI Studio (Gemini API) khi có Internet.
    Trả về câu trả lời nếu thành công, hoặc None nếu mất mạng / hết quota để tự động fallback sang Ollama.
    """
    global _GEMINI_NETWORK_OFFLINE_UNTIL
    if not GEMINI_API_KEY:
        return None

    now_mono = time.monotonic()
    if now_mono < _GEMINI_NETWORK_OFFLINE_UNTIL:
        return None

    available_models = [m for m in GEMINI_MODELS if now_mono >= _GEMINI_COOLDOWN_UNTIL.get(m, 0.0)]
    if not available_models:
        available_models = list(GEMINI_MODELS)

    payload = json.dumps(
        {
            "systemInstruction": {"parts": [{"text": system_content}]},
            "contents": _build_gemini_contents(raw_message, prefs),
            "generationConfig": _build_gemini_generation_config(raw_message, prefs),
        }
    ).encode("utf-8")

    for model_name in available_models:
        url = f"{GEMINI_BASE_URL}/{model_name}:generateContent?key={GEMINI_API_KEY}"
        try:
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=3.5) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            candidates = data.get("candidates") or []
            if not candidates:
                continue
            parts = ((candidates[0].get("content") or {}).get("parts")) or []
            reply = "".join(
                str(p.get("text") or "")
                for p in parts
                if isinstance(p, dict) and not p.get("thought")
            ).strip()
            if reply:
                reply = _clean_reply_by_prefs(reply, prefs, is_fast_path=False)
                _append_chat_history(raw_message, reply)
                return reply
        except urllib.error.HTTPError as e:
            if e.code == 429:
                _GEMINI_COOLDOWN_UNTIL[model_name] = time.monotonic() + 20.0
                continue
            if e.code == 404:
                _GEMINI_COOLDOWN_UNTIL[model_name] = time.monotonic() + 3600.0
                continue
            if e.code in (500, 502, 503, 504):
                _GEMINI_COOLDOWN_UNTIL[model_name] = time.monotonic() + 10.0
                continue
            if e.code in (400, 401, 403):
                return None
            continue
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            is_net_err = False
            if isinstance(e, urllib.error.URLError):
                reason_str = str(getattr(e, "reason", "")).lower()
                if any(k in reason_str for k in ("gaierror", "getaddrinfo", "nodename nor servname", "network", "refused", "timed out", "10051", "10060", "10065")):
                    is_net_err = True
            elif isinstance(e, (TimeoutError, OSError)):
                err_str = str(e).lower()
                if any(k in err_str for k in ("winerror 10051", "winerror 10060", "winerror 10065", "network", "refused", "timed out")):
                    is_net_err = True
            if is_net_err:
                _GEMINI_NETWORK_OFFLINE_UNTIL = time.monotonic() + 30.0
                return None
            _GEMINI_COOLDOWN_UNTIL[model_name] = time.monotonic() + 10.0
            continue
        except Exception:
            return None
    return None


def _stream_gemini_tokens(raw_message: str, system_content: str, prefs: dict | None = None, is_voice: bool = False):
    """
    Generator stream từng cụm token trực tiếp từ Google AI Studio (SSE), đã qua bộ lọc No-Fluff thời gian thực.
    """
    global _GEMINI_NETWORK_OFFLINE_UNTIL
    if not GEMINI_API_KEY:
        raise RuntimeError("Missing GEMINI_API_KEY")

    now_mono = time.monotonic()
    if now_mono < _GEMINI_NETWORK_OFFLINE_UNTIL:
        raise RuntimeError("Gemini offline circuit breaker active")

    available_models = [m for m in GEMINI_MODELS if now_mono >= _GEMINI_COOLDOWN_UNTIL.get(m, 0.0)]
    if not available_models:
        available_models = list(GEMINI_MODELS)

    # Trong Voice Mode, ưu tiên model có tốc độ phản hồi TTFT cao nhất (gemini-3.1-flash-lite ~720ms)
    if is_voice and "gemini-3.1-flash-lite" in available_models:
        available_models = ["gemini-3.1-flash-lite"] + [m for m in available_models if m != "gemini-3.1-flash-lite"]

    payload = json.dumps(
        {
            "systemInstruction": {"parts": [{"text": system_content}]},
            "contents": _build_gemini_contents(raw_message, prefs),
            "generationConfig": _build_gemini_generation_config(raw_message, prefs),
        }
    ).encode("utf-8")

    last_err: Exception | None = None
    for model_name in available_models:
        url = f"{GEMINI_BASE_URL}/{model_name}:streamGenerateContent?alt=sse&key={GEMINI_API_KEY}"
        full_parts: list[str] = []
        try:
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            def _raw_sse_gen():
                with urllib.request.urlopen(req, timeout=3.5) as resp:
                    for raw_line in resp:
                        line = raw_line.decode("utf-8", errors="replace").strip()
                        if not line.startswith("data:"):
                            continue
                        data_str = line[5:].strip()
                        if not data_str or data_str == "[DONE]":
                            continue
                        chunk_obj = json.loads(data_str)
                        for cand in chunk_obj.get("candidates") or []:
                            parts = ((cand.get("content") or {}).get("parts")) or []
                            for p in parts:
                                if isinstance(p, dict) and not p.get("thought"):
                                    delta = str(p.get("text") or "")
                                    if delta:
                                        yield delta

            for filtered_delta in _filter_stream_tokens(_raw_sse_gen(), prefs):
                full_parts.append(filtered_delta)
                yield filtered_delta

            full_reply = _clean_reply_by_prefs("".join(full_parts).strip(), prefs, is_fast_path=False)
            if full_reply:
                _append_chat_history(raw_message, full_reply)
                return
        except urllib.error.HTTPError as e:
            last_err = e
            if full_parts:
                _append_chat_history(raw_message, _clean_reply_by_prefs("".join(full_parts).strip(), prefs))
                return
            if e.code == 429:
                _GEMINI_COOLDOWN_UNTIL[model_name] = time.monotonic() + 20.0
                continue
            if e.code == 404:
                _GEMINI_COOLDOWN_UNTIL[model_name] = time.monotonic() + 3600.0
                continue
            if e.code in (500, 502, 503, 504):
                _GEMINI_COOLDOWN_UNTIL[model_name] = time.monotonic() + 10.0
                continue
            if e.code in (400, 401, 403):
                break
            continue
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            last_err = e
            is_net_err = False
            if isinstance(e, urllib.error.URLError):
                reason_str = str(getattr(e, "reason", "")).lower()
                if any(k in reason_str for k in ("gaierror", "getaddrinfo", "nodename nor servname", "network", "refused", "timed out", "10051", "10060", "10065")):
                    is_net_err = True
            elif isinstance(e, (TimeoutError, OSError)):
                err_str = str(e).lower()
                if any(k in err_str for k in ("winerror 10051", "winerror 10060", "winerror 10065", "network", "refused", "timed out")):
                    is_net_err = True

            if is_net_err:
                _GEMINI_NETWORK_OFFLINE_UNTIL = time.monotonic() + 30.0
                if full_parts:
                    _append_chat_history(raw_message, _clean_reply_by_prefs("".join(full_parts).strip(), prefs))
                    return
                break  # Ngắt vòng lặp ngay lập tức, chuyển tức thì sang Ollama GPU (<1s)!

            _GEMINI_COOLDOWN_UNTIL[model_name] = time.monotonic() + 10.0
            if full_parts:
                _append_chat_history(raw_message, _clean_reply_by_prefs("".join(full_parts).strip(), prefs))
                return
            continue
        except Exception as e:
            last_err = e
            if full_parts:
                _append_chat_history(raw_message, _clean_reply_by_prefs("".join(full_parts).strip(), prefs))
                return
            break

    raise RuntimeError(f"Gemini streaming unavailable: {last_err}")


def _ask_ollama(
    raw_message: str,
    text_norm: str,
    prebuilt_system: str | None = None,
    prefs: dict | None = None,
) -> str:
    """Gửi câu hỏi sang Ollama local API kèm lịch sử hội thoại và ngữ cảnh thực tế."""
    try:
        model = _resolve_ollama_model()
        if not model:
            return OLLAMA_WAITING_MSG

        p = _normalize_ai_prefs(prefs)
        system_content = prebuilt_system or _build_system_context(text_norm, p, raw_message=raw_message)
        messages = (
            [{"role": "system", "content": system_content}]
            + _get_sanitized_history_for_prefs(p)
            + [{"role": "user", "content": _augment_user_prompt_with_prefs(raw_message, p)}]
        )
        ollama_opts: dict = {"temperature": 0.3 if p["style"] == "concise" else 0.4}

        payload = json.dumps(
            {
                "model": model,
                "messages": messages,
                "stream": False,
                "options": ollama_opts,
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

        reply = _clean_reply_by_prefs(reply, p, is_fast_path=False)
        _append_chat_history(raw_message, reply)
        return reply
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError):
        return OLLAMA_WAITING_MSG
    except Exception:
        return OLLAMA_WAITING_MSG


def _ask_ai(raw_message: str, text_norm: str, prefs: dict | None = None) -> str:
    """Ưu tiên gọi Google AI Studio (Gemini) khi có mạng; tự động fallback về Ollama local khi offline."""
    system_content = _build_system_context(text_norm, prefs, raw_message=raw_message)
    online_reply = _ask_gemini(raw_message, system_content, prefs=prefs)
    if online_reply:
        return online_reply
    return _ask_ollama(raw_message, text_norm, prebuilt_system=system_content, prefs=prefs)


def get_reply(message: str, prefs: dict | None = None) -> str:
    raw = normalize_vietnamese_input(message)
    if not raw:
        return "Gõ gì đó đi, vd: `thời tiết`, `mấy giờ` 😄"
    n = _norm(raw)
    core = _strip_conversational_fillers(n)

    # Tự động học thông tin cá nhân nếu người dùng giới thiệu trong câu chat
    extract_and_update_profile_from_message(raw)

    # --- Xóa lịch sử hội thoại ---
    if core in ("xoa lich su", "clear", "reset", "xoa chat", "xoa hoi thoai"):
        _clear_chat_history()
        return _clean_reply_by_prefs("🧹 Wisp đã xóa lịch sử trò chuyện.", prefs, is_fast_path=True)

    # --- Thời tiết tra cứu ngắn (Fast-path chuẩn SPEC, ưu tiên nơi ở trong Hồ sơ cá nhân) ---
    if _is_short_weather_command(n):
        is_tomorrow = "ngay mai" in n or "tomorrow" in n
        city = _extract_city_after(n, ["thoi tiet", "weather", "nhiet do"])
        if not city:
            city = _detect_city_in_text(n)
        res = get_tomorrow_weather(city) if is_tomorrow else get_current_weather(city)
        cleaned = _clean_reply_by_prefs(res, prefs, is_fast_path=True)
        _append_chat_history(raw, cleaned, is_fast_path=True)
        return cleaned

    # --- Các lệnh tra cứu nhanh (Help, Chào, Giờ, Ngày, Cảm ơn, Tạm biệt) ---
    fast_reply = _match_fast_command(n)
    if fast_reply is not None:
        cleaned = _clean_reply_by_prefs(fast_reply, prefs, is_fast_path=True)
        _append_chat_history(raw, cleaned, is_fast_path=True)
        return cleaned

    # --- Mọi câu hỏi tự nhiên, tư vấn thời tiết, trò chuyện, viết code -> Ưu tiên Gemini Online, fallback Ollama ---
    return _ask_ai(raw, n, prefs=prefs)


def stream_reply(message: str, prefs: dict | None = None, is_voice: bool = False):
    """
    Generator phát luồng sự kiện thời gian thực (NDJSON) phản ánh chính xác
    tiến trình xử lý ở backend & luồng sinh token từ Google AI Studio (khi có mạng) hoặc GPU Ollama (khi offline).
    """
    p = _normalize_ai_prefs(prefs)
    raw = normalize_vietnamese_input(message)
    if not raw:
        yield {"type": "state", "state": "breathing", "label": "Sẵn sàng"}
        yield {"type": "token", "delta": "Gõ gì đó đi, vd: `thời tiết`, `mấy giờ` 😄"}
        yield {"type": "done"}
        return

    n = _norm(raw)
    core = _strip_conversational_fillers(n)

    # Giai đoạn 1: Mở kết nối & kiểm tra bộ nhớ ngữ cảnh
    yield {"type": "state", "state": "connecting", "label": "Đang nạp ngữ cảnh…"}

    # Tự động học thông tin cá nhân nếu người dùng chia sẻ trong câu chat
    learned_fields = extract_and_update_profile_from_message(raw)
    if learned_fields:
        yield {
            "type": "profile_updated",
            "profile": get_user_profile(),
            "learned": learned_fields,
        }

    # Xóa bộ nhớ hội thoại -> trạng thái shaping (biến đổi/tái cấu trúc)
    if core in ("xoa lich su", "clear", "reset", "xoa chat", "xoa hoi thoai"):
        yield {"type": "state", "state": "shaping", "label": "Đang làm mới bộ nhớ hội thoại…"}
        _clear_chat_history()
        cleaned = _clean_reply_by_prefs("🧹 Wisp đã xóa lịch sử trò chuyện.", p, is_fast_path=True)
        yield {"type": "token", "delta": cleaned}
        yield {"type": "done", "reply": cleaned}
        return

    # Fast-path: Thời tiết ngắn -> kích hoạt searching vì thực sự gọi API Open-Meteo
    if _is_short_weather_command(n):
        is_tomorrow = "ngay mai" in n or "tomorrow" in n
        city = _extract_city_after(n, ["thoi tiet", "weather", "nhiet do"]) or _detect_city_in_text(n)
        yield {"type": "state", "state": "searching", "label": "Đang truy xuất dữ liệu Open-Meteo…"}
        res = get_tomorrow_weather(city) if is_tomorrow else get_current_weather(city)
        res = _clean_reply_by_prefs(res, p, is_fast_path=True)
        _append_chat_history(raw, res, is_fast_path=True)
        yield {"type": "state", "state": "composing", "label": "Đang phản hồi…"}
        yield {"type": "token", "delta": res}
        yield {"type": "done", "reply": res}
        return

    # Fast-path: Đồng hồ hệ thống / Giờ quốc tế / Ngày tháng -> trạng thái working
    fast_reply = _match_fast_command(n)
    if fast_reply is not None:
        fast_reply = _clean_reply_by_prefs(fast_reply, p, is_fast_path=True)
        _append_chat_history(raw, fast_reply, is_fast_path=True)
        yield {"type": "state", "state": "working", "label": "Đang xử lý tác vụ nội bộ…"}
        yield {"type": "state", "state": "composing", "label": "Đang phản hồi…"}
        yield {"type": "token", "delta": fast_reply}
        yield {"type": "done", "reply": fast_reply}
        return

    # Chuẩn bị ngữ cảnh thời gian thực, Hồ sơ cá nhân, quy tắc phong cách trả lời & thời tiết (nếu có)
    now = datetime.now(VN_TZ)
    is_code = _is_code_request(raw)
    ctx_lines = [
        _build_system_prompt(),
        _build_user_profile_prompt(),
        _build_style_rules_prompt(p, is_code_query=is_code, is_voice=is_voice),
        f"\n[Ngữ cảnh thời gian thực]: Bây giờ là {now.strftime('%H:%M:%S')} — {WEEKDAY_VI[now.weekday()]}, ngày {now.strftime('%d/%m/%Y')} (giờ Việt Nam).",
    ]

    if _is_weather_topic(raw, n):
        yield {"type": "state", "state": "searching", "label": "Đang lấy dữ liệu thời tiết thực tế…"}
        city = _detect_city_in_text(n)
        is_tomorrow = "ngay mai" in n or "tomorrow" in n
        weather_info = get_tomorrow_weather(city) if is_tomorrow else get_current_weather(city)
        ctx_lines.append(
            f"[Dữ liệu thời tiết thực tế từ Open-Meteo]:\n{weather_info}\n"
            "(Hãy dùng chính xác số liệu và nêu rõ tên địa danh trong dữ liệu thời tiết trên để trả lời hoặc tư vấn phù hợp thực tế)."
        )
        yield {"type": "state", "state": "weaving", "label": "Đang đan ghép dữ liệu vào ngữ cảnh AI…"}

    system_content = "\n".join(ctx_lines)

    # 1. ƯU TIÊN ONLINE: Thử stream qua Google AI Studio (Gemini API) nếu có model chưa bị cooldown
    now_mono = time.monotonic()
    if GEMINI_API_KEY and any(now_mono >= _GEMINI_COOLDOWN_UNTIL.get(m, 0.0) for m in GEMINI_MODELS):
        try:
            yield {"type": "state", "state": "solving", "label": "Đang suy luận qua Google AI Studio…"}
            streamed_parts: list[str] = []
            for delta in _stream_gemini_tokens(raw, system_content, prefs=p, is_voice=is_voice):
                streamed_parts.append(delta)
                yield {"type": "token", "delta": delta}
            if streamed_parts:
                final_clean = _clean_reply_by_prefs("".join(streamed_parts).strip(), p, is_fast_path=False)
                yield {"type": "done", "reply": final_clean}
                return
        except Exception:
            # Mất mạng hoặc Gemini bận -> chuyển mượt sang Ollama local bên dưới
            pass

    # 2. FALLBACK OFFLINE: Luồng AI Local (Ollama Streaming từ GPU)
    try:
        model = _resolve_ollama_model()
        if not model:
            yield {"type": "state", "state": "shaping", "label": "Đang khởi tạo model…"}
            yield {"type": "token", "delta": OLLAMA_WAITING_MSG}
            yield {"type": "done"}
            return

        messages = (
            [{"role": "system", "content": system_content}]
            + _get_sanitized_history_for_prefs(p)
            + [{"role": "user", "content": _augment_user_prompt_with_prefs(raw, p)}]
        )

        yield {"type": "state", "state": "solving", "label": "Đang suy luận trên GPU Local…"}

        ollama_opts: dict = {"temperature": 0.3 if p["style"] == "concise" else 0.4}

        payload = json.dumps(
            {
                "model": model,
                "messages": messages,
                "stream": True,
                "options": ollama_opts,
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            OLLAMA_CHAT_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        full_reply_parts: list[str] = []

        def _raw_ollama_gen():
            with urllib.request.urlopen(req, timeout=60) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    chunk_data = json.loads(line)
                    delta = (chunk_data.get("message") or {}).get("content", "")
                    if delta:
                        yield delta
                    if chunk_data.get("done"):
                        break

        for filtered_delta in _filter_stream_tokens(_raw_ollama_gen(), p):
            full_reply_parts.append(filtered_delta)
            yield {"type": "token", "delta": filtered_delta}

        full_reply = _clean_reply_by_prefs("".join(full_reply_parts).strip(), p, is_fast_path=False)
        if not full_reply:
            yield {"type": "token", "delta": OLLAMA_WAITING_MSG}
            yield {"type": "done"}
        else:
            _append_chat_history(raw, full_reply)
            yield {"type": "done", "reply": full_reply}
    except Exception:
        yield {"type": "token", "delta": OLLAMA_WAITING_MSG}
        yield {"type": "done"}





