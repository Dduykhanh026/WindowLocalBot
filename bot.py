"""
Logic trả lời của bot — không phụ thuộc giao diện.
Kết hợp Fast-path tra cứu tức thì (giờ/ngày/thời tiết chuẩn SPEC),
Ưu tiên AI Online qua Google AI Studio (Gemini API) khi có Internet,
và tự động Fallback về AI Local qua Ollama (qwen2.5:3b) khi mất mạng.
"""
import json
import os
import queue
import re
import socket
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import memory_store
from weather import CITY_ALIASES, get_current_weather, get_multi_day_weather, get_tomorrow_weather

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
# Lưu thời điểm (monotonic) tạm tắt công cụ google_search khi gói Free-Tier hết quota Search Grounding (429/403 riêng cho tool)
_GEMINI_WEB_SEARCH_COOLDOWN_UNTIL: float = 0.0
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
_CHAT_SESSION_EPOCH: int = 0
_PENDING_OVERFLOW_TURNS: list[dict[str, str]] = []
_COMPACTION_SCHEDULED: bool = False
_MEMORY_BG_QUEUE: queue.Queue = queue.Queue()

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

    # 4. Câu hỏi nối tiếp ngắn về ngày mai / ngày mốt / cuối tuần / N ngày tới ngay sau lượt hỏi thời tiết
    if len(n.split()) <= 8 and re.search(
        r"\b(?:ngay\s+mai|tomorrow|ngay\s+mot|cuoi\s+tuan(?:\s+nay|\s+sau)?|\d+\s*ngay\s+toi)\b.*\b(?:the\s+nao|ra\s+sao|sao\s+roi)\b"
        r"|\b(?:con|vay|the)\s+(?:ngay\s+mai|ngay\s+mot|cuoi\s+tuan(?:\s+nay|\s+sau)?|\d+\s*ngay\s+toi)\b",
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
    # Bỏ các mốc thời gian nhiều ngày (3 ngày tới, cuối tuần này, thứ Bảy, ngày mốt...)
    t = re.sub(r"\b\d{1,2}\s*ngay\s*(?:toi|nua|tiep\s+theo|sap\s+toi|qua)?\b", " ", t)
    t = re.sub(
        r"\b(du\s+bao|thoi\s+tiet|ngay\s+mot|ngay\s+kia|cuoi\s+tuan(?:\s+(?:nay|sau|toi))?|"
        r"ca\s+tuan|trong\s+tuan|tuan\s+(?:nay|sau|toi)|"
        r"thu\s*(?:hai|ba|tu|bon|nam|sau|bay|[2-7])(?:\s+(?:nay|sau|toi))?|chu\s*nhat(?:\s+(?:nay|sau|toi))?|"
        r"o|tai|thanh pho|tinh|hien tai|bay gio|hom nay|ngay mai|tomorrow|sap\s+toi|tiep\s+theo|"
        r"the nao|nhu the nao|ra sao|sao roi|bao nhieu do|may do|nhiet do bao nhieu|"
        r"mac gi|an gi|di dau|choi gi|outfit|mang gi|mang o|ao mua|ao khoac|co mua khong|co nang khong|"
        r"nhi|vay|the|nhe|nha|di|a|ha|coi|voi|wisp|oi)\b",
        " ",
        t,
    )
    t = re.sub(r"\s+", " ", t).strip(" ,.?!;:\"'“”")
    return t


def _parse_weather_day_plan(raw_text: str, text_norm: str, now_dt: datetime | None = None) -> dict:
    """
    Phân tích mốc ngày người dùng hỏi thời tiết:
    - "current": hôm nay / hiện tại
    - "tomorrow": ngày mai
    - "multi": ngày mốt, cuối tuần này, cuối tuần sau, thứ cụ thể trong tuần, hoặc N ngày tới (tối đa 7–14 ngày)
    """
    now = now_dt or datetime.now(VN_TZ)
    n = text_norm or _norm(raw_text or "")
    cur_wd = now.weekday()

    # 1. N ngày tới / N ngày tiếp theo (VD: "3 ngày tới", "5 ngày tới", "7 ngày")
    m_ndays = re.search(r"\b(\d{1,2})\s*ngay\s*(?:toi|nua|tiep\s+theo|sap\s+toi)?\b", n)
    if m_ndays:
        n_days = max(1, min(14, int(m_ndays.group(1))))
        if n_days == 1:
            return {"mode": "current", "days": 1, "target_offsets": [0]}
        return {"mode": "multi", "days": n_days, "target_offsets": list(range(n_days))}

    # 2. Cuối tuần sau / cuối tuần tới
    if re.search(r"\bcuoi\s+tuan\s+(?:sau|toi)\b", n):
        days_to_next_mon = 7 - cur_wd
        sat_off = days_to_next_mon + 5
        sun_off = days_to_next_mon + 6
        return {"mode": "multi", "days": sun_off + 1, "target_offsets": [sat_off, sun_off]}

    # 3. Cuối tuần / cuối tuần này
    if re.search(r"\bcuoi\s+tuan(?:\s+nay)?\b", n):
        if cur_wd <= 4:
            offsets = [5 - cur_wd, 6 - cur_wd]
        elif cur_wd == 5:
            offsets = [0, 1]
        else:
            offsets = [0] if "nay" in n else [6, 7]
        return {"mode": "multi", "days": max(offsets) + 1, "target_offsets": offsets}

    # 4. Ngày mốt / ngày kia
    if re.search(r"\b(ngay\s+mot|ngay\s+kia)\b", n):
        return {"mode": "multi", "days": 3, "target_offsets": [2]}

    # 5. Thứ cụ thể trong tuần (VD: "thứ Bảy này", "Chủ Nhật", "thứ Hai tuần sau")
    weekday_patterns = (
        (r"\bthu\s*(?:hai|2)\b", 0),
        (r"\bthu\s*(?:ba|3)\b", 1),
        (r"\bthu\s*(?:tu|bon|4)\b", 2),
        (r"\bthu\s*(?:nam|5)\b", 3),
        (r"\bthu\s*(?:sau|6)\b", 4),
        (r"\bthu\s*(?:bay|7)\b", 5),
        (r"\bchu\s*nhat\b", 6),
    )
    for pat, wd_idx in weekday_patterns:
        if re.search(pat, n):
            if re.search(r"\btuan\s+(?:sau|toi)\b", n):
                off = (7 - cur_wd) + wd_idx
            else:
                off = (wd_idx - cur_wd) % 7
            if off == 1 and not re.search(r"\btuan\s+(?:sau|toi)\b", n):
                # Nếu vẫn muốn hiển thị rõ thứ/ngày cho ngày được hỏi theo thứ, dùng multi 1-ngày
                return {"mode": "multi", "days": 7, "target_offsets": [off]}
            return {"mode": "multi", "days": max(7, off + 1), "target_offsets": [off]}

    # 6. Cả tuần / dự báo tuần này / tuần tới
    if re.search(r"\b(ca\s+tuan|trong\s+tuan|tuan\s+nay|tuan\s+sau|tuan\s+toi)\b", n):
        return {"mode": "multi", "days": 7, "target_offsets": None}

    # 7. Ngày mai
    if "ngay mai" in n or "tomorrow" in n:
        return {"mode": "tomorrow", "days": 2, "target_offsets": [1]}

    return {"mode": "current", "days": 1, "target_offsets": [0]}


def _get_weather_for_query(
    city: str,
    raw_text: str,
    text_norm: str,
    now_dt: datetime | None = None,
) -> str:
    """Lấy dữ liệu thời tiết đúng mốc ngày (hiện tại / ngày mai / 7 ngày) cho cả Fast-path và System Context."""
    plan = _parse_weather_day_plan(raw_text, text_norm, now_dt=now_dt)
    if plan["mode"] == "tomorrow":
        return get_tomorrow_weather(city)
    if plan["mode"] == "multi":
        return get_multi_day_weather(city, days=plan["days"], target_offsets=plan["target_offsets"])
    return get_current_weather(city)


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
    2. Cấu trúc '... của' / '... cua' (vd: 'Bố của mình', 'ban than cua minh'): chặn toàn bộ, trừ danh từ chỉ thuộc tính người dùng.
    3. Cấu trúc sở hữu đẳng lập và thân tộc mở rộng có dấu & không dấu (vd: 'Con gái mình', 'ban than minh', 'sep minh').
    """
    prefix = text[:start_pos].rstrip()
    if not prefix:
        return False

    # 1. Bỏ qua lời chào xã giao hướng tới listener khi người dùng không gõ dấu phẩy
    if re.search(r"\b(?:chào|chao|hi|hello|alo|ê|e|hey)\s+(?:bạn|ban|anh|em|bot)$", prefix, flags=re.IGNORECASE):
        return False

    prefix_norm = _norm(prefix)

    # 2. Xử lý giới từ sở hữu 'của' (cả có dấu và không dấu)
    if re.search(r"\bcủa$", prefix, flags=re.IGNORECASE) or re.search(r"\bcua$", prefix_norm):
        if re.search(
            r"\b(?:chieu\s+cao|can\s+nang|nam\s+sinh|tuoi|ten)"
            r"(?:\s+(?:hien\s+tai|chinh\s+xac|thuc\s+te))?\s+cua$",
            prefix_norm,
        ):
            return False
        return True

    # 3. Xử lý danh từ thân tộc, quan hệ xã hội, vật nuôi, tài sản (có dấu & không dấu)
    rel_pattern = rf"\b{_RELATIVE_NOUNS}(?:\s+nhà)?$"
    if re.search(rel_pattern, prefix, flags=re.IGNORECASE):
        return True

    unaccented_rel_pattern = (
        r"\b(?:ban(?:\s+(?:than|be|gai|trai|cung\s+phong|cung\s+lop))?|"
        r"nguoi\s+yeu(?:\s+cu)?|crush|vo|chong|ba\s+xa|ong\s+xa|"
        r"(?:bo|ba|cha|me|ma)(?:\s+(?:chong|vo|ruot|nuoi))?|"
        r"(?:anh|chi|em)(?:\s+(?:ruot|ho|dau|re|trai|gai))?|"
        r"(?:con|chau|be)(?:\s+(?:gai|trai|nho|cung))?|"
        r"ong|ba|bac|chu|co|di|cau|mo|sep|dong\s+nghiep|nhan\s+vien|"
        r"thay(?:\s+giao)?|co\s+giao|cho|meo|thu\s+cung|pet)(?:\s+nha)?$"
    )
    return bool(re.search(unaccented_rel_pattern, prefix_norm))


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

    if updated:
        # Chỉ lưu nếu thực sự có giá trị thay đổi so với hồ sơ hiện tại
        real_changes = {k: v for k, v in updated.items() if str(prof.get(k) or "") != str(v)}
        if real_changes:
            update_user_profile(real_changes)
            return real_changes
    return {}


# ==============================================================================
# TRÍ NHỚ DÀI HẠN 2 TẦNG (SQLITE) & TÓM TẮT CUỘN HỘI THOẠI DÀI
# ==============================================================================

_FENCED_CODE_BLOCK_RE = re.compile(r"(`{3,})[\s\S]*?(?:\1`*|$)")
_INLINE_CODE_UNWRAP_RE = re.compile(r"``((?:(?!``)[^\n])+)``|`([^`\n]+)`")

_VN_PROSE_OR_CMD_LEAD_RE = re.compile(
    r"^(?:(?:ê|e|alo|chào|chao|hi|hello)\s+)?(?:wisp\s+)?(?:ơi\s+|oi\s+)?"
    r"(?:hãy\s+|hay\s+|nhớ\s+|nho\s+|làm\s+ơn\s+|lam\s+on\s+)?"
    r"(?:nhắc|nhac|thêm|them|tạo|tao|lưu|luu|xong|làm\s+xong|lam\s+xong|"
    r"hoàn\s+thành|hoan\s+thanh|đánh\s+dấu|danh\s+dau|quên|quen|xóa|xoa|"
    r"đừng\s+nhớ|dung\s+nho|ghi\s+nhớ|ghi\s+nho|mình|minh|tôi|toi)\b",
    flags=re.IGNORECASE,
)

_RAW_CODE_START_RE = re.compile(
    r"^\s*(?:let|var|const|func|function|def|class|struct|enum|extension|protocol|interface|type|typedef|"
    r"import|export|return|yield|if|for|while|switch|case|guard|async|await|try|catch|finally|throw|"
    r"public|private|protected|static|final|void|int|double|float|bool|String|Widget|"
    r"@[A-Za-z_]\w*|//|/\*|\*/|#include|#define|#pragma|<\?[A-Za-z]|</?[A-Za-z][A-Za-z0-9_.-]*(?:\s|>|/>))",
)

_QUESTION_CLAUSE_RE = re.compile(
    r"\?\s*$|\b(?:là\s+gì|tên\s+gì|tên\s+là\s+gì|ăn\s+gì|uống\s+gì|mặc\s+gì|làm\s+gì|"
    r"việc\s+gì|cái\s+gì|ở\s+đâu|khi\s+nào|bao\s+giờ|mấy\s+giờ|bao\s+nhiêu|thế\s+nào|"
    r"ra\s+sao|như\s+thế\s+nào|có\s+nhớ\s+không|nhớ\s+gì|không\s+nhỉ|chưa\s+nhỉ|sao\s+nhỉ|"
    r"la\s+gi|ten\s+gi|ten\s+la\s+gi|an\s+gi|uong\s+gi|mac\s+gi|lam\s+gi|viec\s+gi|"
    r"cai\s+gi|o\s+dau|khi\s+nao|bao\s+gio|may\s+gio|bao\s+nhieu|the\s+nao|nhu\s+the\s+nao|"
    r"co\s+nho\s+khong|nho\s+gi|khong\s+nhi|chua\s+nhi|sao\s+nhi)\b",
    flags=re.IGNORECASE,
)

_LEADING_TASK_TIME_RE = re.compile(
    r"^(?:(?:vào|vao|lúc|luc|tầm|tam|khoảng|khoang)\s+)?(?:(?:buổi|buoi)\s+)?"
    r"(?:"
    r"(?:sáng|sang|trưa|trua|chiều|chieu|tối|toi|đêm|dem)\s+"
    r"(?:hôm\s+nay|hom\s+nay|ngày\s+mai|ngay\s+mai|ngày\s+mốt|ngay\s+mot|mai|mốt|mot|nay|"
    r"(?:thứ|thu)\s+(?:hai|ba|tư|tu|bốn|bon|năm|nam|sáu|sau|bảy|bay|[2-7])(?:\s+(?:tuần|tuan)\s+(?:này|nay|sau|tới|toi))?|"
    r"(?:chủ\s+nhật|chu\s+nhat)(?:\s+(?:tuần|tuan)\s+(?:này|nay|sau|tới|toi))?)"
    r"(?:\s+(?:(?:lúc|luc)\s+)?\d{1,2}\s*(?:h|giờ|gio|:)\s*(?:\d{1,2}\s*(?:phút|phut|p)?|rưỡi|ruoi)?)?"
    r"|(?:hôm\s+nay|hom\s+nay|ngày\s+mai|ngay\s+mai|ngày\s+mốt|ngay\s+mot|mai|(?:cuối\s+tuần|cuoi\s+tuan)(?:\s+(?:này|nay|sau|tới|toi))?|"
    r"(?:thứ|thu)\s+(?:hai|ba|tư|tu|bốn|bon|năm|nam|sáu|sau|bảy|bay|[2-7])(?:\s+(?:tuần|tuan)\s+(?:này|nay|sau|tới|toi))?|"
    r"(?:chủ\s+nhật|chu\s+nhat)(?:\s+(?:tuần|tuan)\s+(?:này|nay|sau|tới|toi))?|(?:ngày|ngay)\s+\d{1,2}[/-]\d{1,2}(?:[/-]\d{4})?)"
    r"(?:\s+(?:vào\s+|vao\s+|lúc\s+|luc\s+)?(?:(?:buổi|buoi)\s+)?(?:sáng|sang|trưa|trua|chiều|chieu|tối|toi|đêm|dem))?"
    r"(?:\s+(?:(?:lúc|luc)\s+)?\d{1,2}\s*(?:h|giờ|gio|:)\s*(?:\d{1,2}\s*(?:phút|phut|p)?|rưỡi|ruoi)?)?"
    r"|\d{1,2}\s*(?:h|giờ|gio|:)\s*(?:\d{1,2}\s*(?:phút|phut|p)?|rưỡi|ruoi)?"
    r"(?:\s+(?:sáng|sang|trưa|trua|chiều|chieu|tối|toi|đêm|dem))?"
    r"(?:\s+(?:hôm\s+nay|hom\s+nay|ngày\s+mai|ngay\s+mai|ngày\s+mốt|ngay\s+mot|mai|nay|(?:ngày|ngay)\s+\d{1,2}[/-]\d{1,2}(?:[/-]\d{4})?))?"
    r"|\d{1,3}\s*(?:tiếng|tieng|giờ|gio|phút|phut|ngày|ngay)\s+(?:nữa|nua|tới|toi)"
    r")\s*[,:-]?\s*",
    flags=re.IGNORECASE,
)

_TRAILING_TASK_TIME_RE = re.compile(
    r"\s+(?:vào|vao|lúc|luc)\s+(?:(?:buổi|buoi)\s+)?(?:(?:sáng|sang|trưa|trua|chiều|chieu|tối|toi|đêm|dem)\s+)?"
    r"(?:hôm\s+nay|hom\s+nay|ngày\s+mai|ngay\s+mai|ngày\s+mốt|ngay\s+mot|mai|(?:cuối\s+tuần|cuoi\s+tuan)(?:\s+(?:này|nay|sau|tới|toi))?|"
    r"(?:thứ|thu)\s+(?:hai|ba|tư|tu|bốn|bon|năm|nam|sáu|sau|bảy|bay|[2-7])(?:\s+(?:tuần|tuan)\s+(?:này|nay|sau|tới|toi))?|"
    r"(?:chủ\s+nhật|chu\s+nhat)(?:\s+(?:tuần|tuan)\s+(?:này|nay|sau|tới|toi))?|(?:ngày|ngay)\s+\d{1,2}[/-]\d{1,2}(?:[/-]\d{4})?|"
    r"\d{1,2}\s*(?:h|giờ|gio|:)\s*(?:\d{1,2})?.*)$",
    flags=re.IGNORECASE,
)


def _is_pure_source_code_message(msg: str) -> bool:
    """
    Chỉ chặn tin nhắn thực sự là mã nguồn / câu lệnh CLI / SQL,
    KHÔNG chặn nhầm câu tiếng Việt tự nhiên có chứa ký hiệu '->', '=', '>=' (VD: 'Ghi nhớ: chuyển UIKit -> SwiftUI', 'BMI = 22').
    """
    stripped = (msg or "").strip()
    if not stripped:
        return True
    if _RAW_CODE_START_RE.search(stripped) or _LIKELY_SQL_OR_CLI_RE.search(stripped):
        return True
    if "\n" in stripped and all(_is_likely_code_line(ln, True) for ln in stripped.splitlines() if ln.strip()):
        return True
    if _VN_PROSE_OR_CMD_LEAD_RE.search(stripped):
        return False
    # Nếu câu có dấu tiếng Việt rõ ràng và không có ngoặc nhọn/chấm phẩy kết thúc code
    if re.search(r"[àáảãạăắằẳẵặâấầẩẫậđèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵ]", stripped.lower()):
        if not re.search(r"[{}]|\);\s*$", stripped):
            return False
    return _is_likely_code_line(stripped, False)


def _clean_extracted_task_title(raw_body: str, has_parsed_due: bool) -> str:
    body = re.sub(
        r"\s+(?:nhé|nhe|nha|giúp\s+mình|giup\s+minh|cho\s+mình|cho\s+minh|với\s+nhé|voi\s+nhe|với\s+nha|voi\s+nha|ạ|nhá)$",
        "",
        raw_body.strip(" \t\r\n.!;"),
        flags=re.IGNORECASE,
    ).strip(" ,:-")
    if has_parsed_due:
        stripped = _LEADING_TASK_TIME_RE.sub("", body).strip(" ,:-")
        stripped = _TRAILING_TASK_TIME_RE.sub("", stripped).strip(" ,:-")
        stripped = re.sub(r"^(?:phải|phai|cần|can|nhớ|nho|là)\s+", "", stripped, flags=re.IGNORECASE).strip()
        if len(stripped.split()) >= 2:
            body = stripped
    return _capitalize_first_char(body)


def _split_compound_memory_clauses(clause_text: str) -> list[str]:
    """Tách câu ghép tiếng Việt (có dấu & không dấu) có nhiều mệnh đề thông tin cá nhân độc lập."""
    parts = re.split(
        r"\s*(?:;\s*|\s+(?:và|va|đồng\s+thời|dong\s+thoi|ngoài\s+ra|ngoai\s+ra)\s+"
        r"(?=(?:(?:mình|minh|tôi|toi|anh|em)\s+)?(?:đang\s+|dang\s+|vừa\s+|vua\s+|rất\s+|rat\s+|khá\s+|kha\s+|cũng\s+|cung\s+|thường\s+|thuong\s+|hay\s+|bị\s+|bi\s+|có\s+thói\s+quen\s+|co\s+thoi\s+quen\s+)?"
        r"(?:làm|lam|xây\s+dựng|xay\s+dung|phát\s+triển|phat\s+trien|code|viết|viet|"
        r"thích|thich|ghét|ghet|mê|chuộng|chuong|không\s+thích|khong\s+thich|"
        r"không\s+uống|khong\s+uong|không\s+ăn|khong\s+an|dị\s+ứng|di\s+ung|kiêng|kieng|"
        r"ăn\s+chay|an\s+chay|đau\s+dạ\s+dày|dau\s+da\s+day|chạy\s+bộ|chay\s+bo|tập|tap|"
        r"thói\s+quen|thoi\s+quen|chuyên\s+dùng|chuyen\s+dung|ưu\s+tiên|uu\s+tien)))\s*",
        clause_text.strip(),
        flags=re.IGNORECASE,
    )
    cleaned: list[str] = []
    for p in parts:
        c = p.strip(" \t\r\n,.!;")
        c = re.sub(r"^(?:rằng|rang|là)\s+", "", c, flags=re.IGNORECASE)
        c = re.sub(r"^(?:mình|minh|tôi|toi|tớ)\s+", "", c, flags=re.IGNORECASE)
        c = re.sub(r"\s+(?:nhé|nhe|nha|ạ|nhá|đó|đấy)$", "", c, flags=re.IGNORECASE).strip()
        if len(c) >= 5 and not _QUESTION_CLAUSE_RE.search(c):
            cleaned.append(_capitalize_first_char(c))
    return cleaned


def extract_instant_memory_and_tasks(raw_message: str, now_dt: datetime | None = None) -> dict:
    """
    Tầng 1 (< 1ms): Nhận diện & lưu tức thì các lệnh Nhắc việc, Hoàn thành việc, Quên ký ức,
    Ghi nhớ tường minh và câu kể thông tin cá nhân (sở thích / dự án / sức khỏe / thói quen, cả có dấu & không dấu).
    """
    result: dict = {
        "memories_added": [],
        "memories_forgotten": [],
        "tasks_added": [],
        "tasks_completed": [],
        "direct_reply": None,
        "toast": "",
    }
    if not raw_message:
        return result

    without_fences = _FENCED_CODE_BLOCK_RE.sub(" ", raw_message).strip()
    if not without_fences:
        return result
    msg = _INLINE_CODE_UNWRAP_RE.sub(lambda m: (m.group(1) or m.group(2) or "").strip(), without_fences).strip()
    if not msg or _is_pure_source_code_message(msg):
        return result

    msg_single = re.sub(r"\s+", " ", msg).strip()
    norm_msg = _norm(msg_single)

    # Tránh can thiệp vào lệnh xóa lịch sử chat
    if _strip_conversational_fillers(norm_msg) in ("xoa lich su", "clear", "reset", "xoa chat", "xoa hoi thoai"):
        return result

    # 1. Lệnh Nhắc việc / Thêm việc cần nhớ
    m_remind = re.match(
        r"^(?:(?:ê|e|alo|chào|chao)\s+)?(?:wisp\s+)?(?:(?:ơi|oi)\s+)?(?:hãy\s+|hay\s+|nhớ\s+|nho\s+|làm\s+ơn\s+|lam\s+on\s+)?"
        r"(?:nhắc\s+(?:mình|tôi|anh|em)|nhac\s+(?:minh|toi|anh|em)|"
        r"(?:thêm|them|tạo|tao|lưu|luu)\s+(?:việc\s+cần\s+(?:làm|nhớ)|viec\s+can\s+(?:lam|nho)|lời\s+nhắc|loi\s+nhac|nhắc\s+việc|nhac\s+viec|task))\s*[:,-]?\s+(.+)$",
        msg_single,
        flags=re.IGNORECASE,
    )
    if m_remind and not msg_single.endswith("?"):
        task_body = m_remind.group(1).strip()
        due_text, due_iso = memory_store.parse_vietnamese_due_time(task_body, now_dt=now_dt)
        clean_title = _clean_extracted_task_title(task_body, has_parsed_due=bool(due_text))
        if len(clean_title) >= 2:
            task = memory_store.add_task(
                title=clean_title,
                due_text=due_text,
                due_iso=due_iso,
                now_dt=now_dt,
            )
            if task:
                result["tasks_added"].append(task)
                due_badge = f" (⏰ {task['due_text']})" if task.get("due_text") else ""
                result["toast"] = f"📌 Đã thêm việc cần nhớ: {task['title']}"
                result["direct_reply"] = f"📌 Đã thêm việc cần nhớ: **{task['title']}**{due_badge}."
                return result

    # 2. Lệnh Đánh dấu hoàn thành việc cần nhớ
    m_done = re.match(
        r"^(?:wisp\s+(?:(?:ơi|oi)\s+)?)?(?:(?:mình|minh|tôi|toi|anh|em)\s+)?(?:đã\s+|da\s+|vừa\s+|vua\s+)?"
        r"(?:xong\s+việc|làm\s+xong\s+việc|hoàn\s+thành(?:\s+việc)?|đánh\s+dấu\s+(?:xong|hoàn\s+thành)|"
        r"xong\s+viec|lam\s+xong\s+viec|hoan\s+thanh(?:\s+viec)?|danh\s+dau\s+(?:xong|hoan\s+thanh))\s*[:,-]?\s*"
        r"(.+?)(?:\s+rồi|\s+roi)?(?:\s+(?:nhé|nhe|nha|ạ))?[.!]*$",
        msg_single,
        flags=re.IGNORECASE,
    )
    if m_done and not msg_single.endswith("?"):
        target_q = m_done.group(1).strip(" \"'“”")
        completed = memory_store.complete_task_by_query(target_q, now_dt=now_dt)
        if completed:
            result["tasks_completed"].extend(completed)
            result["toast"] = f"✅ Đã hoàn thành: {completed[0]['title']}"
            result["direct_reply"] = f"✅ Tuyệt vời! Wisp đã đánh dấu hoàn thành việc: **{completed[0]['title']}**."
        else:
            result["direct_reply"] = f"ℹ️ Mình chưa tìm thấy việc đang chờ nào khớp với “{target_q}” trong danh sách Việc cần nhớ."
        return result

    # 3. Lệnh Quên / Xóa ký ức
    m_forget = re.match(
        r"^(?:wisp\s+(?:(?:ơi|oi)\s+)?)?(?:hãy\s+|hay\s+|làm\s+ơn\s+|lam\s+on\s+)?"
        r"(?:quên\s+(?:chuyện|việc|thông\s+tin|ký\s+ức|rằng|là)?|xóa\s+ký\s+ức(?:\s+về)?|đừng\s+nhớ\s+(?:chuyện|việc)?|"
        r"quen\s+(?:chuyen|viec|thong\s+tin|ky\s+uc|rang|la)?|xoa\s+ky\s+uc(?:\s+ve)?|dung\s+nho\s+(?:chuyen|viec)?)\s*[:,-]?\s*"
        r"(.+?)(?:\s+(?:đi|di))?(?:\s+(?:nhé|nhe|nha|giúp\s+mình|giup\s+minh|ạ))?[.!]*$",
        msg_single,
        flags=re.IGNORECASE,
    )
    if m_forget and not msg_single.endswith("?"):
        forget_q = re.sub(r"\s+(?:đi|di|giúp\s+mình|giup\s+minh)$", "", m_forget.group(1).strip(), flags=re.IGNORECASE).strip(" \"'“”")
        deleted = memory_store.forget_memories_by_query(forget_q)
        if deleted:
            result["memories_forgotten"].extend(deleted)
            result["toast"] = f"🗑️ Wisp đã quên {len(deleted)} ký ức"
            if len(deleted) == 1:
                result["direct_reply"] = f"🗑️ Wisp đã xóa khỏi trí nhớ dài hạn: “{deleted[0]['content']}”."
            else:
                result["direct_reply"] = f"🗑️ Wisp đã xóa {len(deleted)} ký ức liên quan đến “{forget_q}” khỏi trí nhớ dài hạn."
        else:
            result["direct_reply"] = f"ℹ️ Trong trí nhớ dài hạn hiện không có ký ức nào khớp với “{forget_q}”."
        return result

    # 4a. Lệnh Ghi nhớ tường minh ("Ghi nhớ: ...", "Hãy nhớ giúp mình: ...")
    m_explicit_mem = re.match(
        r"^(?:wisp\s+(?:(?:ơi|oi)\s+)?)?(?:hãy\s+|hay\s+)?"
        r"(?:ghi\s+nhớ|nhớ\s+giúp\s+(?:mình|tôi|anh|em)|lưu\s+ký\s+ức|ghi\s+nho|nho\s+giup\s+(?:minh|toi|anh|em)|luu\s+ky\s+uc)"
        r"(?:\s+(?:giúp\s+mình|giup\s+minh|cho\s+mình|cho\s+minh|rằng|rang|là|nhé|nhe))?\s*[:,-]\s*(.+)$|"
        r"^(?:wisp\s+(?:(?:ơi|oi)\s+)?)?(?:hãy\s+|hay\s+)?(?:ghi\s+nhớ|ghi\s+nho)\s+(?:giúp\s+mình\s+|giup\s+minh\s+|cho\s+mình\s+|cho\s+minh\s+)?(?:rằng\s+|rang\s+|là\s+)?(.+)$",
        msg_single,
        flags=re.IGNORECASE,
    )
    if m_explicit_mem and not msg_single.endswith("?"):
        raw_fact = (m_explicit_mem.group(1) or m_explicit_mem.group(2) or "").strip()
        clauses = _split_compound_memory_clauses(raw_fact) or [_capitalize_first_char(raw_fact.strip(" .!"))]
        for cl in clauses:
            saved = memory_store.upsert_memory(cl, category="auto", importance=4, source="fast_rule", now_dt=now_dt)
            if saved:
                result["memories_added"].append(saved)
        if result["memories_added"]:
            joined = "; ".join(m["content"] for m in result["memories_added"])
            result["toast"] = f"🧠 Wisp đã ghi nhớ: {result['memories_added'][0]['content']}"
            result["direct_reply"] = f"🧠 Wisp đã lưu vào trí nhớ dài hạn: **{joined}**."
            return result

    # 4b. Tự học nhanh từ câu kể tự nhiên ngôi thứ nhất (khi bật auto_learn, hỗ trợ cả gõ có dấu & không dấu)
    prof = get_user_profile()
    if not prof.get("auto_learn", True):
        return result

    for sent in re.split(r"(?<=[.!?\n])\s+", msg):
        sent_clean = sent.strip()
        if not sent_clean or _QUESTION_CLAUSE_RE.search(sent_clean):
            continue

        for m_self in re.finditer(
            r"\b(?P<pron>mình|minh|tôi|toi|(?:anh(?!\s+(?:ấy|ay|ta|đó|do|họ|ho)))|(?:em(?!\s+(?:ấy|ay|ta|đó|do|họ|ho))))\s+"
            r"(?P<pred>"
            r"(?:(?:đang|dang|vừa|vua|sắp|sap)\s+)?(?:làm|lam|xây\s+dựng|xay\s+dung|phát\s+triển|phat\s+trien|viết|viet|code|triển\s+khai|trien\s+khai)\s+"
            r"(?:dự\s+án|du\s+an|project|ứng\s+dụng|ung\s+dung|app|sản\s+phẩm|san\s+pham|website|thư\s+viện|thu\s+vien|tool)\s+[^.!?\n]{2,160}?(?=(?:,\s*(?:mình|minh|tôi|toi)\s+)|[.!?\n]|$)"
            r"|(?:(?:rất|rat|khá|kha|cực\s+kỳ|cuc\s+ky)\s+)?(?:thích|thich|ghét|ghet|mê|chuộng|chuong|yêu\s+thích|yeu\s+thich|không\s+thích|khong\s+thich)\s+[^.!?\n]{3,150}?(?=(?:,\s*(?:mình|minh|tôi|toi)\s+)|[.!?\n]|$)"
            r"|(?:(?:bị|bi)\s+)?(?:dị\s+ứng(?:\s+với)?|di\s+ung(?:\s+voi)?|không\s+uống\s+được|khong\s+uong\s+duoc|không\s+ăn\s+được|khong\s+an\s+duoc|ăn\s+chay|an\s+chay|kiêng|kieng|đau\s+dạ\s+dày|dau\s+da\s+day|mất\s+ngủ|mat\s+ngu)\s*[^.!?\n]{2,140}?(?=(?:,\s*(?:mình|minh|tôi|toi)\s+)|[.!?\n]|$)"
            r"|(?:có\s+thói\s+quen|co\s+thoi\s+quen|thường\s+xuyên|thuong\s+xuyen|(?:mỗi|moi)\s+(?:sáng|sang|tối|toi)\s+(?:thường|thuong)|(?:hàng|hang)\s+(?:ngày|ngay)\s+(?:thường|thuong))\s+[^.!?\n]{3,150}?(?=(?:,\s*(?:mình|minh|tôi|toi)\s+)|[.!?\n]|$)"
            r"|(?:chuyên\s+dùng|chuyen\s+dung|ưu\s+tiên\s+dùng|uu\s+tien\s+dung|đang\s+dùng\s+stack|dang\s+dung\s+stack|đang\s+học|dang\s+hoc)\s+[^.!?\n]{2,140}?(?=(?:,\s*(?:mình|minh|tôi|toi)\s+)|[.!?\n]|$)"
            r")",
            sent_clean,
            flags=re.IGNORECASE,
        ):
            # Tránh bắt tên riêng 'Minh' ở ngôi thứ 3 giữa câu (VD: 'Hôm qua Minh thích ăn phở')
            pron_raw = m_self.group("pron")
            if pron_raw == "Minh" and m_self.start() > 0 and sent_clean[: m_self.start()].rstrip()[-1:] not in (",", ";"):
                continue
            if _is_preceded_by_possessive(sent_clean, m_self.start()):
                continue
            pred_text = m_self.group("pred").strip()
            clauses = _split_compound_memory_clauses(pred_text)
            for cl in clauses:
                saved = memory_store.upsert_memory(cl, category="auto", importance=3, source="fast_rule", now_dt=now_dt)
                if saved:
                    result["memories_added"].append(saved)

    if result["memories_added"]:
        if len(result["memories_added"]) == 1:
            result["toast"] = f"🧠 Wisp đã ghi nhớ: {result['memories_added'][0]['content']}"
        else:
            result["toast"] = f"🧠 Wisp đã ghi nhớ {len(result['memories_added'])} thông tin mới"

    return result


def _build_memory_context_prompt(query: str, now_dt: datetime | None = None) -> str:
    """Truy xuất Ký ức dài hạn, Việc cần nhớ đang chờ & Tóm tắt phiên dài để đưa vào System Prompt."""
    try:
        bundle = memory_store.retrieve_context_bundle(
            query=query,
            now_dt=now_dt,
            max_memories=8,
            max_tasks=6,
            mark_used=True,
        )
    except Exception:
        return ""

    memories = bundle.get("memories") or []
    tasks = bundle.get("tasks") or []
    summaries = bundle.get("summaries") or []

    if not memories and not tasks and not summaries:
        return ""

    sections: list[str] = []
    if memories:
        mem_lines = ["\n[KÝ ỨC DÀI HẠN LIÊN QUAN CỦA CHỦ NHÂN (SQLITE MEMORY)]:"]
        for m in memories:
            pin_tag = "📌 Ghim | " if m.get("pinned") else ""
            mem_lines.append(f"- [{pin_tag}{m.get('category', 'fact')}] {m.get('content', '')}")
        sections.append("\n".join(mem_lines))

    if tasks:
        task_lines = ["\n[VIỆC CẦN NHỚ & KẾ HOẠCH CÁ NHÂN ĐANG CHỜ]:"]
        for t in tasks:
            due_part = f" (Thời hạn: {t['due_text']})" if t.get("due_text") else ""
            task_lines.append(f"- {t.get('title', '')}{due_part}")
        sections.append("\n".join(task_lines))

    if summaries:
        sum_lines = ["\n[TÓM TẮT MẠCH CHUYỆN TRƯỚC TRONG PHIÊN NÀY]:"]
        for s in summaries:
            sum_lines.append(f"- {s.get('summary', '')}")
        sections.append("\n".join(sum_lines))

    sections.append(
        "(HƯỚNG DẪN TRÍ NHỚ CHỦ ĐỘNG: Khi chủ nhân hỏi về dự án, sở thích, thói quen, lịch trình/việc cần làm "
        "hoặc xin tư vấn thực tế, hãy chủ động kết nối các dữ liệu Ký ức dài hạn, Việc cần nhớ, Thời gian thực "
        "và Thời tiết ở trên để đưa ra câu trả lời chính xác, tinh tế và cá nhân hóa. Không liệt kê máy móc các ký ức không liên quan)."
    )
    return "\n".join(sections)


def _answer_personal_memory_offline(
    raw_message: str,
    text_norm: str,
    mem_events: dict | None = None,
    now_dt: datetime | None = None,
) -> str | None:
    """
    Phản hồi thông minh từ SQLite cục bộ trong trường hợp máy hoàn toàn mất mạng VÀ chưa bật Ollama:
    - Nếu người dùng vừa kể thông tin cá nhân mới (đã lưu ở Tầng 1): xác nhận đã ghi nhớ.
    - Nếu người dùng hỏi lại về dự án / sở thích / việc cần làm của chính mình: tra trực tiếp từ SQLite.
    """
    n = text_norm or _norm(raw_message)
    added_mems = (mem_events or {}).get("memories_added") or []
    if added_mems and not _QUESTION_CLAUSE_RE.search(raw_message):
        joined = "; ".join(m["content"] for m in added_mems)
        return f"🧠 Mình đã ghi nhớ vào bộ nhớ dài hạn: **{joined}**."

    has_self_ref = bool(re.search(r"\b(minh|toi|cua minh|cho minh)\b", n))
    asks_mem = bool(
        re.search(
            r"\b(du an|project|app|ung dung|so thich|thich gi|kieng|di ung|thoi quen|nho gi|ten gi|ten la gi)\b",
            n,
        )
    )
    asks_task = bool(
        re.search(
            r"\b(viec gi|viec can|can lam|can nho|lich|ke hoach|nhac nho)\b",
            n,
        )
    )
    if not (has_self_ref and (asks_mem or asks_task)):
        return None

    bundle = memory_store.retrieve_context_bundle(raw_message, now_dt=now_dt, max_memories=4, max_tasks=4)
    mems = bundle.get("memories") or []
    tasks = bundle.get("tasks") or []
    if not mems and not tasks:
        return None

    lines: list[str] = []
    if asks_mem and mems:
        top_mems = [m["content"] for m in mems[:2]]
        lines.append(f"🧠 Theo trí nhớ dài hạn của mình: **{'; '.join(top_mems)}**.")
    if asks_task and tasks:
        t_parts = [
            f"**{t['title']}**" + (f" (⏰ {t['due_text']})" if t.get("due_text") else "")
            for t in tasks[:3]
        ]
        lines.append(f"📌 Việc cần nhớ đang chờ: {'; '.join(t_parts)}.")
    if not lines and mems:
        lines.append(f"🧠 Theo trí nhớ của mình: **{mems[0]['content']}**.")
    return "\n".join(lines) if lines else None


def _extract_json_object(raw_text: str) -> dict | None:
    """Tách an toàn JSON object từ đầu ra của Gemini hoặc Ollama (kể cả khi được bọc trong ```json)."""
    if not raw_text:
        return None
    s = raw_text.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*```$", "", s)
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(s[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _call_llm_json(prompt: str, system_instruction: str = "") -> dict | None:
    """
    Gọi nhanh LLM ở chế độ JSON chuẩn hóa cho tác vụ chạy ngầm:
    Ưu tiên Gemini Flash-Lite (thinkingBudget=0, responseMimeType=application/json, tự động bỏ thinkingConfig nếu HTTP 400),
    tự động fallback sang Ollama local (format=json) khi offline.
    """
    now_mono = time.monotonic()
    if GEMINI_API_KEY and now_mono >= _GEMINI_NETWORK_OFFLINE_UNTIL:
        bg_models = ["gemini-3.1-flash-lite", *[m for m in GEMINI_MODELS if m != "gemini-3.1-flash-lite"]]
        for model_name in bg_models:
            if now_mono < _GEMINI_COOLDOWN_UNTIL.get(model_name, 0.0):
                continue
            url = f"{GEMINI_BASE_URL}/{model_name}:generateContent?key={GEMINI_API_KEY}"
            for include_thinking in (True, False):
                gen_cfg: dict = {
                    "temperature": 0.1,
                    "responseMimeType": "application/json",
                }
                if include_thinking:
                    gen_cfg["thinkingConfig"] = {"thinkingBudget": 0}
                body: dict = {
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": gen_cfg,
                }
                if system_instruction:
                    body["systemInstruction"] = {"parts": [{"text": system_instruction}]}
                try:
                    req = urllib.request.Request(
                        url,
                        data=json.dumps(body).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=5.0) as resp:
                        data = json.loads(resp.read().decode("utf-8", errors="replace"))
                    cands = data.get("candidates") or []
                    if cands:
                        parts = ((cands[0].get("content") or {}).get("parts")) or []
                        txt = "".join(
                            str(p.get("text") or "")
                            for p in parts
                            if isinstance(p, dict) and not p.get("thought")
                        ).strip()
                        obj = _extract_json_object(txt)
                        if obj is not None:
                            return obj
                    break
                except urllib.error.HTTPError as e:
                    try:
                        e.close()
                    except Exception:
                        pass
                    if e.code == 400 and include_thinking:
                        continue
                    break
                except Exception:
                    break

    # Fallback sang Ollama Local ở chế độ format="json"
    try:
        model = _resolve_ollama_model()
        if not model:
            return None
        msgs = []
        if system_instruction:
            msgs.append({"role": "system", "content": system_instruction})
        msgs.append({"role": "user", "content": prompt})
        payload = json.dumps(
            {
                "model": model,
                "messages": msgs,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.1},
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            OLLAMA_CHAT_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        txt = ((data.get("message") or {}).get("content") or "").strip()
        return _extract_json_object(txt)
    except Exception:
        return None


def _has_personal_memory_signal(user_msg: str) -> bool:
    """Kiểm tra nhanh câu chat có tín hiệu thông tin cá nhân/dự án/sở thích/kế hoạch đáng để Tầng 2 AI phân tích không."""
    if not user_msg:
        return False
    cleaned = _CODE_SEGMENT_RE.sub(" ", user_msg).strip()
    if len(cleaned.split()) < 4:
        return False
    n = _norm(cleaned)
    if not re.search(r"\b(minh|toi|anh|em|du an|project|app|so thich|thoi quen|ke hoach|lich|tuan sau|ngay mai)\b", n):
        return False
    return bool(
        re.search(
            r"\b(du an|project|app|ung dung|thich|ghet|di ung|kieng|thoi quen|thuong|dang lam|chuyen sang|"
            r"doi sang|bo|het|khong con|quen|nhac|ke hoach|lich|sap|se|dinh|muon|hoc|dung|stack|vo|chong|con|gia dinh)\b",
            n,
        )
    )


def _run_background_memory_learning_sync(user_msg: str, assistant_msg: str = "") -> list[dict]:
    """
    Tầng 2 (Chạy ngầm sau lượt chat): Dùng LLM JSON rút trích & hợp nhất Ký ức / Việc cần nhớ
    (hỗ trợ add_memory, update_memory khi thông tin cũ thay đổi, delete_memory, add_task, complete_task).
    """
    if not get_user_profile().get("auto_learn", True):
        return []
    cleaned_user = _CODE_SEGMENT_RE.sub(" ", user_msg or "").strip()
    if not cleaned_user:
        return []

    bundle = memory_store.retrieve_context_bundle(
        cleaned_user, max_memories=10, max_tasks=8, mark_used=False
    )
    existing_mems = [
        {"id": m["id"], "category": m["category"], "content": m["content"]}
        for m in (bundle.get("memories") or [])
    ]
    existing_tasks = [
        {"id": t["id"], "title": t["title"], "due_text": t["due_text"]}
        for t in (bundle.get("tasks") or [])
    ]

    sys_inst = (
        "Bạn là bộ trích xuất Trí nhớ Dài hạn của trợ lý cá nhân Wisp. "
        "Chỉ trả về DUY NHẤT một JSON object theo schema: "
        '{"ops": [{"op": "add_memory|update_memory|delete_memory|add_task|complete_task", ...}]}.'
    )
    prompt = (
        f"KÝ ỨC HIỆN CÓ:\n{json.dumps(existing_mems, ensure_ascii=False)}\n\n"
        f"VIỆC ĐANG CHỜ HIỆN CÓ:\n{json.dumps(existing_tasks, ensure_ascii=False)}\n\n"
        f"TIN NHẮN MỚI CỦA NGƯỜI DÙNG:\n{cleaned_user}\n\n"
        "QUY TẮC:\n"
        "1. Chỉ lưu sự thật bền vững về CHÍNH người dùng (category thuộc: preference, project, skill, habit, health, relationship, fact; importance 1..5) hoặc việc cần nhớ cụ thể.\n"
        "2. KHÔNG trích xuất câu hỏi thông thường, thời tiết hay thông tin đã có y hệt trong KÝ ỨC HIỆN CÓ.\n"
        "3. Nếu tin nhắn mới CẬP NHẬT/THAY ĐỔI một ký ức cũ (ví dụ đổi công nghệ, đổi sở thích), phát lệnh "
        '{"op": "update_memory", "id": <id_cu>, "category": "...", "content": "<noi_dung_moi>", "importance": 4}.\n'
        "4. Nếu người dùng báo một ký ức cũ không còn đúng hoặc bảo quên đi, phát lệnh "
        '{"op": "delete_memory", "id": <id_cu>}.\n'
        "5. Nếu có kế hoạch/việc cần làm mới chưa có trong VIỆC ĐANG CHỜ, phát lệnh "
        '{"op": "add_task", "title": "...", "due_text": "..."}. Nếu báo đã làm xong việc đang chờ, phát lệnh {"op": "complete_task", "id": <id_task>}.\n'
        '6. Nếu không có gì mới cần cập nhật, trả về {"ops": []}.'
    )

    parsed = _call_llm_json(prompt, system_instruction=sys_inst)
    if not isinstance(parsed, dict):
        return []
    ops = parsed.get("ops")
    if not isinstance(ops, list):
        return []

    applied: list[dict] = []
    for raw_op in ops[:6]:
        if not isinstance(raw_op, dict):
            continue
        op_type = str(raw_op.get("op") or "").strip().lower()
        try:
            if op_type == "add_memory":
                content = str(raw_op.get("content") or "").strip()
                cat = str(raw_op.get("category") or "auto").strip().lower()
                imp = int(raw_op.get("importance") or 3)
                if content:
                    res = memory_store.upsert_memory(content, category=cat, importance=imp, source="ai_bg")
                    if res:
                        applied.append({"op": "add_memory", "item": res})
            elif op_type == "update_memory":
                mem_id = raw_op.get("id")
                content = str(raw_op.get("content") or "").strip()
                cat = str(raw_op.get("category") or "").strip().lower() or None
                imp = int(raw_op["importance"]) if "importance" in raw_op and raw_op["importance"] is not None else None
                if mem_id is not None and content:
                    res = memory_store.update_memory(int(mem_id), content=content, category=cat, importance=imp)
                    if res is None:
                        res = memory_store.upsert_memory(content, category=cat or "auto", importance=imp or 3, source="ai_bg")
                    if res:
                        applied.append({"op": "update_memory", "item": res})
            elif op_type == "delete_memory":
                mem_id = raw_op.get("id")
                query_str = str(raw_op.get("query") or raw_op.get("content") or "").strip()
                if mem_id is not None:
                    if memory_store.delete_memory(int(mem_id)):
                        applied.append({"op": "delete_memory", "id": int(mem_id)})
                elif query_str:
                    deleted = memory_store.forget_memories_by_query(query_str)
                    if deleted:
                        applied.append({"op": "delete_memory", "deleted": deleted})
            elif op_type == "add_task":
                title = str(raw_op.get("title") or "").strip()
                due_hint = str(raw_op.get("due_text") or "").strip()
                if title:
                    d_txt, d_iso = memory_store.parse_vietnamese_due_time(due_hint or title)
                    res = memory_store.add_task(title=title, due_text=d_txt or due_hint, due_iso=d_iso)
                    if res:
                        applied.append({"op": "add_task", "item": res})
            elif op_type == "complete_task":
                tid = raw_op.get("id")
                q_str = str(raw_op.get("title") or raw_op.get("query") or "").strip()
                if tid is not None:
                    res = memory_store.update_task_status(int(tid), "done")
                    if res:
                        applied.append({"op": "complete_task", "item": res})
                elif q_str:
                    done_list = memory_store.complete_task_by_query(q_str)
                    if done_list:
                        applied.append({"op": "complete_task", "item": done_list[0]})
        except Exception:
            continue
    return applied


def _summarize_overflow_turns_locally(turns: list[dict]) -> str:
    """Tóm tắt cục bộ súc tích (2–3 câu) khi offline để không mất mạch hội thoại dài."""
    user_snippets: list[str] = []
    assistant_snippets: list[str] = []
    for item in turns:
        role = item.get("role")
        content = re.sub(r"\s+", " ", str(item.get("content") or "")).strip()
        if not content:
            continue
        short = content[:95].rstrip() + ("…" if len(content) > 95 else "")
        if role == "user" and len(user_snippets) < 3:
            user_snippets.append(short)
        elif role == "assistant" and len(assistant_snippets) < 2:
            assistant_snippets.append(short)
    if not user_snippets:
        return ""
    part_u = "; ".join(user_snippets)
    if assistant_snippets:
        return f"Đầu buổi trò chuyện, người dùng đã trao đổi về: {part_u}. Wisp đã phản hồi: {assistant_snippets[-1]}"
    return f"Đầu buổi trò chuyện, người dùng đã trao đổi về: {part_u}."


def _run_episodic_compaction_sync(expected_epoch: int) -> dict | None:
    """Tóm tắt các lượt hội thoại cũ bị tràn khỏi cửa sổ MAX_HISTORY_TURNS vào bảng episodic_summaries."""
    global _COMPACTION_SCHEDULED
    with _CHAT_HISTORY_LOCK:
        if expected_epoch != _CHAT_SESSION_EPOCH:
            _PENDING_OVERFLOW_TURNS.clear()
            _COMPACTION_SCHEDULED = False
            return None
        turns = list(_PENDING_OVERFLOW_TURNS)
        _PENDING_OVERFLOW_TURNS.clear()
        _COMPACTION_SCHEDULED = False

    if not turns:
        return None

    transcript_lines = []
    for t in turns[-12:]:
        who = "Người dùng" if t.get("role") == "user" else "Wisp"
        txt = re.sub(r"\s+", " ", str(t.get("content") or "")).strip()[:220]
        if txt:
            transcript_lines.append(f"- {who}: {txt}")
    if not transcript_lines:
        return None

    summary_text = ""
    parsed = _call_llm_json(
        "Hãy tóm tắt đoạn hội thoại trước đó dưới đây thành 2 câu tiếng Việt thật ngắn gọn, giữ lại chủ đề chính và quyết định quan trọng. "
        'Trả về JSON: {"summary": "..."}\n\n' + "\n".join(transcript_lines)
    )
    if isinstance(parsed, dict) and str(parsed.get("summary") or "").strip():
        summary_text = str(parsed["summary"]).strip()
    else:
        summary_text = _summarize_overflow_turns_locally(turns)

    if not summary_text:
        return None

    with _CHAT_HISTORY_LOCK:
        if expected_epoch != _CHAT_SESSION_EPOCH:
            return None
    return memory_store.add_episodic_summary(summary_text)


def _schedule_background_memory_learning(user_msg: str, assistant_msg: str = "") -> bool:
    """Đẩy tác vụ tự học Tầng 2 vào hàng đợi nền (0ms latency penalty cho luồng chat chính)."""
    if not get_user_profile().get("auto_learn", True):
        return False
    if not _has_personal_memory_signal(user_msg):
        return False
    _MEMORY_BG_QUEUE.put_nowait(("learn", user_msg, assistant_msg))
    return True


def _compact_overflow_history_async(expected_epoch: int) -> None:
    """Đẩy tác vụ tóm tắt cuộn hội thoại dài vào hàng đợi nền."""
    _MEMORY_BG_QUEUE.put_nowait(("compact", expected_epoch, ""))


def _memory_bg_worker_loop() -> None:
    while True:
        item = _MEMORY_BG_QUEUE.get()
        try:
            if not isinstance(item, tuple) or not item:
                continue
            kind = item[0]
            if kind == "learn":
                _run_background_memory_learning_sync(str(item[1]), str(item[2]))
            elif kind == "compact":
                _run_episodic_compaction_sync(int(item[1]))
        except Exception:
            pass
        finally:
            _MEMORY_BG_QUEUE.task_done()


_MEMORY_BG_THREAD = threading.Thread(target=_memory_bg_worker_loop, name="wisp-memory-bg", daemon=True)
_MEMORY_BG_THREAD.start()


def wait_for_background_memory(timeout: float = 3.0) -> bool:
    """Chờ hàng đợi tác vụ trí nhớ nền xử lý xong (hữu ích cho kiểm thử tự động)."""
    deadline = time.monotonic() + max(0.05, timeout)
    while time.monotonic() < deadline:
        if _MEMORY_BG_QUEUE.unfinished_tasks == 0:
            return True
        time.sleep(0.02)
    return _MEMORY_BG_QUEUE.unfinished_tasks == 0


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
    if any(kw in text_norm for kw in ("thoi tiet", "weather", "nhiet do", "du bao", "bao nhieu do", "may do")):
        extracted = _extract_city_after(text_norm, ["thoi tiet", "weather", "nhiet do", "du bao", "bao nhieu do", "may do"])
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
    if not any(kw in core for kw in ("thoi tiet", "weather", "nhiet do", "du bao", "bao nhieu do", "may do")):
        return False
    # Loại trừ các câu hỏi tư vấn sâu cần LLM suy luận (mặc gì, ăn gì, đi chơi đâu, tại sao...)
    advisory_without_how = tuple(
        k for k in _ADVISORY_KEYWORDS if k not in ("the nao", "ra sao", "bao nhieu do", "may do")
    )
    if any(adv in core for adv in advisory_without_how):
        return False
    if len(core.split()) > 12:
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
    grounding_footer = ""
    s = text
    m_src = re.search(r"(\n\n(?:🌐\s*)?Nguồn:\s+.+)$", s)
    if m_src:
        grounding_footer = m_src.group(1)
        s = s[: m_src.start()]

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
        if grounding_footer:
            grounding_footer = re.sub(r"🌐\s*", "", grounding_footer)

    out = (s.strip() + grounding_footer).strip()
    return out or text.strip()


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


def _build_system_context(
    text_norm: str,
    prefs: dict | None = None,
    raw_message: str = "",
    is_voice: bool = False,
    preloaded_weather: str | None = None,
) -> str:
    """
    Hợp nhất ngữ cảnh chủ động cho System Prompt:
    [HỒ SƠ CHỦ NHÂN] + [KÝ ỨC DÀI HẠN LIÊN QUAN] + [VIỆC CẦN NHỚ & KẾ HOẠCH]
    + [TÓM TẮT MẠCH CHUYỆN TRƯỚC] + [QUY TẮC PHONG CÁCH] + [THỜI GIAN THỰC] + [DỰ BÁO THỜI TIẾT ĐÚNG MỐC NGÀY].
    """
    now = datetime.now(VN_TZ)
    query_for_ctx = raw_message or text_norm
    is_code = _is_code_request(query_for_ctx)
    ctx_lines = [
        _build_system_prompt(),
        _build_user_profile_prompt(),
    ]
    mem_ctx = _build_memory_context_prompt(query_for_ctx, now_dt=now)
    if mem_ctx:
        ctx_lines.append(mem_ctx)

    ctx_lines.append(_build_style_rules_prompt(prefs or DEFAULT_AI_PREFS, is_code_query=is_code, is_voice=is_voice))
    ctx_lines.append(
        f"\n[Ngữ cảnh thời gian thực]: Bây giờ là {now.strftime('%H:%M:%S')} — "
        f"{WEEKDAY_VI[now.weekday()]}, ngày {now.strftime('%d/%m/%Y')} (giờ Việt Nam)."
    )

    weather_info = preloaded_weather
    if weather_info is None and _is_weather_topic(raw_message, text_norm):
        city = _detect_city_in_text(text_norm)
        weather_info = _get_weather_for_query(city, raw_message, text_norm, now_dt=now)

    if weather_info:
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
    global _COMPACTION_SCHEDULED
    if not str(user_msg or "").strip() or not str(assistant_msg or "").strip():
        return
    max_msgs = MAX_HISTORY_TURNS * 2
    should_schedule = False
    epoch_snapshot = 0
    with _CHAT_HISTORY_LOCK:
        _CHAT_HISTORY.append({"role": "user", "content": user_msg})
        _CHAT_HISTORY.append({"role": "assistant", "content": assistant_msg, "is_fast_path": is_fast_path})
        if len(_CHAT_HISTORY) > max_msgs:
            overflow = _CHAT_HISTORY[:-max_msgs]
            del _CHAT_HISTORY[:-max_msgs]
            _PENDING_OVERFLOW_TURNS.extend(overflow)
            if len(_PENDING_OVERFLOW_TURNS) > 24:
                del _PENDING_OVERFLOW_TURNS[:-24]
            if not _COMPACTION_SCHEDULED and len(_PENDING_OVERFLOW_TURNS) >= 2:
                _COMPACTION_SCHEDULED = True
                should_schedule = True
                epoch_snapshot = _CHAT_SESSION_EPOCH
    if should_schedule:
        _compact_overflow_history_async(epoch_snapshot)


def _clear_chat_history() -> None:
    """
    Xóa lịch sử hội thoại ngắn hạn (_CHAT_HISTORY) và bản tóm tắt phiên (episodic_summaries),
    BẢO TOÀN 100% kho Ký ức dài hạn (memories), Việc cần nhớ (tasks) và Hồ sơ cá nhân (user_profile.json).
    """
    global _GEMINI_NETWORK_OFFLINE_UNTIL, _GEMINI_WEB_SEARCH_COOLDOWN_UNTIL, _CHAT_SESSION_EPOCH, _COMPACTION_SCHEDULED
    with _CHAT_HISTORY_LOCK:
        _CHAT_SESSION_EPOCH += 1
        _CHAT_HISTORY.clear()
        _PENDING_OVERFLOW_TURNS.clear()
        _COMPACTION_SCHEDULED = False
    try:
        memory_store.clear_episodic_summaries()
    except Exception:
        pass
    _GEMINI_COOLDOWN_UNTIL.clear()
    _GEMINI_WEB_SEARCH_COOLDOWN_UNTIL = 0.0
    _GEMINI_NETWORK_OFFLINE_UNTIL = 0.0


def clear_chat_history() -> None:
    """Xóa sạch toàn bộ lịch sử hội thoại trong bộ nhớ backend (giữ nguyên Trí nhớ dài hạn & Việc cần nhớ)."""
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


# ==============================================================================
# SUY LUẬN THÍCH ỨNG (ADAPTIVE THINKING) & WEB SEARCH GROUNDING
# ==============================================================================

_DEEP_REASONING_NORM_RE = re.compile(
    r"\b("
    r"kien truc|architecture|clean architecture|mvvm|viper|tca|riverpod|bloc|state management|"
    r"concurrency|async|await|actor|race condition|deadlock|memory leak|retain cycle|"
    r"performance|toi uu|refactor|design pattern|microservice|database schema|migration|"
    r"traceback|exception|stack trace|crash|segfault|nullpointer|debug|sua loi|fix bug|tai sao loi|"
    r"thuat toan|do phuc tap|big-o|big o|quy hoach dong|chung minh|phuong trinh|tich phan|dao ham|xac suat|"
    r"so sanh chuyen sau|phan tich chuyen sau|phan tich uu nhuoc diem|lap ke hoach|lo trinh|chien luoc"
    r")\b",
    flags=re.IGNORECASE,
)


def _classify_reasoning_plan(
    raw_message: str,
    text_norm: str = "",
    prefs: dict | None = None,
    is_voice: bool = False,
) -> dict:
    """
    Phân tầng suy luận thích ứng (Adaptive Thinking):
    - Voice Mode hoặc câu hỏi ngắn thường ngày -> thinking_budget = 0 (phản hồi tức thì).
    - Câu hỏi kiến trúc phần mềm, debug lỗi khó, thuật toán/toán/logic, phân tích chuyên sâu, lập kế hoạch
      -> thinking_budget = 2048 (hoặc 4096 khi style == 'detailed'), ưu tiên gemini-3.5-flash.
    """
    if is_voice:
        return {
            "thinking_budget": 0,
            "preferred_model": "gemini-3.1-flash-lite",
            "tier": "instant",
            "reason": "voice_mode",
        }

    raw = raw_message or ""
    n = text_norm or _norm(raw)
    p = _normalize_ai_prefs(prefs)
    deep_budget = 4096 if p["style"] == "detailed" else 2048

    has_code_fence = "```" in raw
    code_line_count = sum(1 for ln in raw.splitlines() if _is_likely_code_line(ln, "\n" in raw))
    word_count = len(raw.split())

    is_deep = False
    reason = "casual_or_short"

    if has_code_fence or code_line_count >= 2:
        is_deep = True
        reason = "code_block_analysis"
    elif _DEEP_REASONING_NORM_RE.search(n):
        is_deep = True
        reason = "complex_engineering_or_logic"
    elif _is_code_request(raw) and word_count >= 7:
        is_deep = True
        reason = "code_generation"
    elif word_count >= 25 and re.search(r"\b(phan tich|so sanh|thiet ke|giai thich chi tiet|danh gia)\b", n):
        is_deep = True
        reason = "multi_step_analysis"

    if is_deep:
        return {
            "thinking_budget": deep_budget,
            "preferred_model": "gemini-3.5-flash",
            "tier": "deep",
            "reason": reason,
        }

    return {
        "thinking_budget": 0,
        "preferred_model": "gemini-3.5-flash",
        "tier": "instant",
        "reason": reason,
    }


def _needs_web_search_grounding(raw_message: str, text_norm: str = "") -> bool:
    """
    Phát hiện câu hỏi cần tra cứu Web thời gian thực qua Gemini Google Search Grounding:
    - Hỏi tin tức/sự kiện mới, giá vàng/tỷ giá/bitcoin/thị trường hôm nay
    - Hỏi phiên bản mới nhất / bản cập nhật hiện nay của công nghệ, thư viện, hệ điều hành
    - Yêu cầu tường minh: 'tra trên mạng', 'tìm web', 'tìm google', 'mới nhất hiện nay'
    """
    if not raw_message:
        return False
    raw_no_code = _CODE_SEGMENT_RE.sub(" ", raw_message).strip()
    if not raw_no_code:
        return False
    n = text_norm or _norm(raw_no_code)

    # Không bật Web Search cho câu hỏi về bản thân chủ nhân hoặc thời tiết địa phương (đã có Open-Meteo)
    if re.search(r"\b(cua minh|du an minh|viec gi can lam|bmi cua minh|minh bao nhieu tuoi|minh nang|minh cao)\b", n):
        return False

    explicit_web = bool(
        re.search(
            r"\b(tra tren mang|tim tren mang|tim tren web|tim web|tra cuu web|tim google|google giup|search web|tra mang|nguon tin moi)\b",
            n,
        )
    )
    if explicit_web:
        return True

    if _is_weather_topic(raw_no_code, n) and not re.search(r"\b(tin tuc|bao so|ap thap)\b", n):
        return False

    live_news_or_market = bool(
        re.search(
            r"\b(tin tuc|thoi su|su kien moi|vua ra mat|moi cong bo|gia vang|ty gia|gia bitcoin|gia btc|gia xang|chung khoan hom nay|ket qua tran)\b",
            n,
        )
    )
    if live_news_or_market:
        return True

    latest_version_or_trends = bool(
        re.search(
            r"\b(phien ban|version|release|ban cap nhat)\b.*\b(moi nhat|hien nay|hien tai|gan nhat)\b"
            r"|\b(moi nhat)\b.*\b(phien ban|version|release|hien nay|hien tai)\b"
            r"|\b(swift|swiftui|xcode|ios|macos|flutter|dart|python|react|nextjs|kotlin|android|windows|gemini|openai|claude)\b"
            r".*\b(moi nhat|hien nay co gi|nam 2025|nam 2026|2026 co gi)\b",
            n,
        )
    )
    return latest_version_or_trends


def _extract_grounding_sources(candidate: dict, max_sources: int = 4) -> list[tuple[str, str]]:
    """Trích xuất danh sách (title, uri) an toàn từ groundingMetadata.groundingChunks của Gemini."""
    if not isinstance(candidate, dict):
        return []
    meta = candidate.get("groundingMetadata")
    if not isinstance(meta, dict):
        return []
    chunks = meta.get("groundingChunks")
    if not isinstance(chunks, list):
        return []

    sources: list[tuple[str, str]] = []
    seen_uris: set[str] = set()
    seen_titles: set[str] = set()

    for ch in chunks:
        if not isinstance(ch, dict):
            continue
        web = ch.get("web")
        if not isinstance(web, dict):
            continue
        uri = str(web.get("uri") or "").strip()
        if not re.match(r"^https?://[^\s<>\"']+$", uri, flags=re.IGNORECASE):
            continue
        raw_title = re.sub(r"[\[\]\r\n]+", " ", str(web.get("title") or "")).strip()
        raw_title = re.sub(r"\s+", " ", raw_title)
        if not raw_title:
            m_host = re.match(r"^https?://([^/:?#]+)", uri, flags=re.IGNORECASE)
            raw_title = m_host.group(1) if m_host else "Nguồn Web"
        if len(raw_title) > 60:
            raw_title = raw_title[:57].rstrip() + "…"

        uri_key = uri.lower()
        title_key = raw_title.lower()
        if uri_key in seen_uris or title_key in seen_titles:
            continue
        seen_uris.add(uri_key)
        seen_titles.add(title_key)
        sources.append((raw_title, uri))
        if len(sources) >= max_sources:
            break
    return sources


def _format_grounding_sources_footer(sources: list[tuple[str, str]], plain_text: bool = False) -> str:
    if not sources:
        return ""
    if plain_text:
        joined = " · ".join(f"{title} ({uri})" for title, uri in sources)
        return f"\n\nNguồn: {joined}"
    links = " · ".join(f"[{title}]({uri})" for title, uri in sources)
    return f"\n\n🌐 Nguồn: {links}"


def _build_gemini_generation_config(
    raw_message: str,
    prefs: dict | None = None,
    thinking_budget: int | None = None,
    is_voice: bool = False,
    include_thinking_config: bool = True,
) -> dict:
    p = _normalize_ai_prefs(prefs)
    if thinking_budget is None:
        plan = _classify_reasoning_plan(raw_message, _norm(raw_message), prefs=p, is_voice=is_voice)
        budget = int(plan["thinking_budget"])
    else:
        budget = max(0, int(thinking_budget))
    cfg: dict = {
        "temperature": 0.35 if p["style"] == "concise" else 0.45,
    }
    if include_thinking_config:
        cfg["thinkingConfig"] = {"thinkingBudget": budget}
    return cfg


def _build_gemini_payload_bytes(
    raw_message: str,
    system_content: str,
    prefs: dict | None = None,
    thinking_budget: int = 0,
    use_web_search: bool = False,
    is_voice: bool = False,
    include_thinking_config: bool = True,
) -> bytes:
    body: dict = {
        "systemInstruction": {"parts": [{"text": system_content}]},
        "contents": _build_gemini_contents(raw_message, prefs),
        "generationConfig": _build_gemini_generation_config(
            raw_message,
            prefs=prefs,
            thinking_budget=thinking_budget,
            is_voice=is_voice,
            include_thinking_config=include_thinking_config,
        ),
    }
    if use_web_search:
        body["tools"] = [{"google_search": {}}]
    return json.dumps(body).encode("utf-8")


def _build_gemini_attempt_chain(
    raw_message: str,
    system_content: str,
    prefs: dict,
    budget: int,
    enable_web: bool,
    is_voice: bool = False,
    is_streaming: bool = False,
) -> list[tuple[bytes, float, bool]]:
    """
    Xây dựng chuỗi payload hạ cấp an toàn `(payload_bytes, timeout_sec, has_web_tool)`:
    1. Nếu bật Web Search và chưa bị cooldown quota Search Grounding: thử payload đầy đủ (`google_search` + `thinkingConfig`).
    2. Payload chuẩn không tool (`use_web_search=False`, giữ nguyên `thinkingConfig` với `budget` hiện tại — kể cả `0` hoặc `>0`).
    3. Payload tương thích tối giản: bỏ hoàn toàn cả `tools` lẫn `thinkingConfig`.
    """
    base_timeout = 5.0 if is_streaming else 9.0
    thinking_timeout = 15.0 if budget > 0 else base_timeout
    web_active = bool(enable_web and time.monotonic() >= _GEMINI_WEB_SEARCH_COOLDOWN_UNTIL)

    attempts: list[tuple[bytes, float, bool]] = []
    if web_active:
        attempts.append(
            (
                _build_gemini_payload_bytes(
                    raw_message,
                    system_content,
                    prefs=prefs,
                    thinking_budget=budget,
                    use_web_search=True,
                    is_voice=is_voice,
                    include_thinking_config=True,
                ),
                15.0,
                True,
            )
        )
    attempts.append(
        (
            _build_gemini_payload_bytes(
                raw_message,
                system_content,
                prefs=prefs,
                thinking_budget=budget,
                use_web_search=False,
                is_voice=is_voice,
                include_thinking_config=True,
            ),
            thinking_timeout,
            False,
        )
    )
    attempts.append(
        (
            _build_gemini_payload_bytes(
                raw_message,
                system_content,
                prefs=prefs,
                thinking_budget=0,
                use_web_search=False,
                is_voice=is_voice,
                include_thinking_config=False,
            ),
            base_timeout,
            False,
        )
    )
    return attempts


def _ask_gemini(raw_message: str, system_content: str, prefs: dict | None = None) -> str | None:
    """
    Gọi Google AI Studio (Gemini API) khi có Internet (hỗ trợ Adaptive Thinking + Google Search Grounding).
    Trả về câu trả lời nếu thành công, hoặc None nếu mất mạng / hết quota để tự động fallback sang Ollama.
    """
    global _GEMINI_NETWORK_OFFLINE_UNTIL, _GEMINI_WEB_SEARCH_COOLDOWN_UNTIL
    if not GEMINI_API_KEY:
        return None

    now_mono = time.monotonic()
    if now_mono < _GEMINI_NETWORK_OFFLINE_UNTIL:
        return None

    p = _normalize_ai_prefs(prefs)
    n = _norm(raw_message)
    plan = _classify_reasoning_plan(raw_message, n, prefs=p, is_voice=False)
    budget = int(plan["thinking_budget"])
    use_web_search = _needs_web_search_grounding(raw_message, n)

    available_models = [m for m in GEMINI_MODELS if now_mono >= _GEMINI_COOLDOWN_UNTIL.get(m, 0.0)]
    if not available_models:
        available_models = list(GEMINI_MODELS)

    pref_model = plan.get("preferred_model")
    if pref_model in available_models:
        available_models = [pref_model] + [m for m in available_models if m != pref_model]

    attempts = _build_gemini_attempt_chain(
        raw_message,
        system_content,
        prefs=p,
        budget=budget,
        enable_web=use_web_search,
        is_voice=False,
        is_streaming=False,
    )

    for model_name in available_models:
        url = f"{GEMINI_BASE_URL}/{model_name}:generateContent?key={GEMINI_API_KEY}"
        web_tool_quota_blocked = False

        for attempt_idx, (cur_payload, cur_timeout, has_web_tool) in enumerate(attempts):
            if has_web_tool and time.monotonic() < _GEMINI_WEB_SEARCH_COOLDOWN_UNTIL and attempt_idx + 1 < len(attempts):
                continue
            try:
                req = urllib.request.Request(
                    url,
                    data=cur_payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=cur_timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8", errors="replace"))
                candidates = data.get("candidates") or []
                if not candidates:
                    break
                cand0 = candidates[0]
                parts = ((cand0.get("content") or {}).get("parts")) or []
                reply = "".join(
                    str(part.get("text") or "")
                    for part in parts
                    if isinstance(part, dict) and not part.get("thought")
                ).strip()
                if reply:
                    if web_tool_quota_blocked and not has_web_tool:
                        _GEMINI_WEB_SEARCH_COOLDOWN_UNTIL = time.monotonic() + 180.0
                    sources = _extract_grounding_sources(cand0)
                    footer = _format_grounding_sources_footer(sources, plain_text=bool(p["plain_text"]))
                    reply = _clean_reply_by_prefs(reply, p, is_fast_path=False) + footer
                    _append_chat_history(raw_message, reply)
                    return reply
                break
            except urllib.error.HTTPError as e:
                try:
                    e.close()
                except Exception:
                    pass
                if has_web_tool and e.code in (400, 403, 429) and attempt_idx + 1 < len(attempts):
                    if e.code in (403, 429):
                        web_tool_quota_blocked = True
                    # Hạ cấp bỏ google_search ngay trên CÙNG model (không đặt cooldown model khi gói Free-Tier hết quota tool)
                    continue
                if e.code == 400:
                    if attempt_idx + 1 < len(attempts):
                        # Hạ cấp payload (bỏ hoàn toàn thinkingConfig) trên cùng model
                        continue
                    _GEMINI_COOLDOWN_UNTIL[model_name] = time.monotonic() + 60.0
                    break
                if e.code == 429:
                    _GEMINI_COOLDOWN_UNTIL[model_name] = time.monotonic() + 20.0
                    break
                if e.code == 404:
                    _GEMINI_COOLDOWN_UNTIL[model_name] = time.monotonic() + 3600.0
                    break
                if e.code in (500, 502, 503, 504):
                    _GEMINI_COOLDOWN_UNTIL[model_name] = time.monotonic() + 10.0
                    break
                if e.code in (401, 403):
                    return None
                break
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
                break
            except Exception:
                return None
    return None


def _stream_gemini_tokens(
    raw_message: str,
    system_content: str,
    prefs: dict | None = None,
    is_voice: bool = False,
    reasoning_plan: dict | None = None,
    use_web_search: bool | None = None,
):
    """
    Generator stream từng cụm token trực tiếp từ Google AI Studio (SSE),
    hỗ trợ Adaptive Thinking + Google Search Grounding kèm trích nguồn xác thực.
    """
    global _GEMINI_NETWORK_OFFLINE_UNTIL, _GEMINI_WEB_SEARCH_COOLDOWN_UNTIL
    if not GEMINI_API_KEY:
        raise RuntimeError("Missing GEMINI_API_KEY")

    now_mono = time.monotonic()
    if now_mono < _GEMINI_NETWORK_OFFLINE_UNTIL:
        raise RuntimeError("Gemini offline circuit breaker active")

    p = _normalize_ai_prefs(prefs)
    n = _norm(raw_message)
    plan = reasoning_plan or _classify_reasoning_plan(raw_message, n, prefs=p, is_voice=is_voice)
    budget = int(plan["thinking_budget"])
    enable_web = _needs_web_search_grounding(raw_message, n) if use_web_search is None else bool(use_web_search)

    available_models = [m for m in GEMINI_MODELS if now_mono >= _GEMINI_COOLDOWN_UNTIL.get(m, 0.0)]
    if not available_models:
        available_models = list(GEMINI_MODELS)

    pref_model = "gemini-3.1-flash-lite" if is_voice else plan.get("preferred_model")
    if pref_model in available_models:
        available_models = [pref_model] + [m for m in available_models if m != pref_model]

    attempts = _build_gemini_attempt_chain(
        raw_message,
        system_content,
        prefs=p,
        budget=budget,
        enable_web=enable_web,
        is_voice=is_voice,
        is_streaming=True,
    )

    last_err: Exception | None = None
    for model_name in available_models:
        url = f"{GEMINI_BASE_URL}/{model_name}:streamGenerateContent?alt=sse&key={GEMINI_API_KEY}"

        abort_all_models = False
        web_tool_quota_blocked = False
        for attempt_idx, (cur_payload, cur_timeout, has_web_tool) in enumerate(attempts):
            if has_web_tool and time.monotonic() < _GEMINI_WEB_SEARCH_COOLDOWN_UNTIL and attempt_idx + 1 < len(attempts):
                continue
            full_parts: list[str] = []
            collected_sources: list[tuple[str, str]] = []

            def _finalize_partial_stream() -> bool:
                global _GEMINI_WEB_SEARCH_COOLDOWN_UNTIL
                if not full_parts:
                    return False
                partial_reply = _clean_reply_by_prefs("".join(full_parts).strip(), p, is_fast_path=False)
                if not partial_reply:
                    return False
                if web_tool_quota_blocked and not has_web_tool:
                    _GEMINI_WEB_SEARCH_COOLDOWN_UNTIL = time.monotonic() + 180.0
                _append_chat_history(raw_message, partial_reply)
                return True

            try:
                req = urllib.request.Request(
                    url,
                    data=cur_payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )

                def _raw_sse_gen():
                    with urllib.request.urlopen(req, timeout=cur_timeout) as resp:
                        for raw_line in resp:
                            line = raw_line.decode("utf-8", errors="replace").strip()
                            if not line.startswith("data:"):
                                continue
                            data_str = line[5:].strip()
                            if not data_str or data_str == "[DONE]":
                                continue
                            chunk_obj = json.loads(data_str)
                            for cand in chunk_obj.get("candidates") or []:
                                srcs = _extract_grounding_sources(cand)
                                for s_item in srcs:
                                    if all(s_item[1].lower() != ex[1].lower() and s_item[0].lower() != ex[0].lower() for ex in collected_sources):
                                        if len(collected_sources) < 4:
                                            collected_sources.append(s_item)
                                parts = ((cand.get("content") or {}).get("parts")) or []
                                for part in parts:
                                    if isinstance(part, dict) and not part.get("thought"):
                                        delta = str(part.get("text") or "")
                                        if delta:
                                            yield delta

                for filtered_delta in _filter_stream_tokens(_raw_sse_gen(), p):
                    full_parts.append(filtered_delta)
                    yield filtered_delta

                full_reply = _clean_reply_by_prefs("".join(full_parts).strip(), p, is_fast_path=False)
                if full_reply:
                    if web_tool_quota_blocked and not has_web_tool:
                        _GEMINI_WEB_SEARCH_COOLDOWN_UNTIL = time.monotonic() + 180.0
                    footer = _format_grounding_sources_footer(collected_sources, plain_text=bool(p["plain_text"]))
                    if footer:
                        full_parts.append(footer)
                        full_reply = full_reply + footer
                        yield footer
                    _append_chat_history(raw_message, full_reply)
                    return
                break
            except urllib.error.HTTPError as e:
                try:
                    e.close()
                except Exception:
                    pass
                last_err = e
                if _finalize_partial_stream():
                    return
                if has_web_tool and e.code in (400, 403, 429) and attempt_idx + 1 < len(attempts):
                    if e.code in (403, 429):
                        web_tool_quota_blocked = True
                    continue
                if e.code == 400:
                    if attempt_idx + 1 < len(attempts):
                        continue
                    _GEMINI_COOLDOWN_UNTIL[model_name] = time.monotonic() + 60.0
                    break
                if e.code == 429:
                    _GEMINI_COOLDOWN_UNTIL[model_name] = time.monotonic() + 20.0
                    break
                if e.code == 404:
                    _GEMINI_COOLDOWN_UNTIL[model_name] = time.monotonic() + 3600.0
                    break
                if e.code in (500, 502, 503, 504):
                    _GEMINI_COOLDOWN_UNTIL[model_name] = time.monotonic() + 10.0
                    break
                if e.code in (401, 403):
                    abort_all_models = True
                    break
                break
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
                    if _finalize_partial_stream():
                        return
                    abort_all_models = True
                    break  # Ngắt vòng lặp ngay lập tức, chuyển tức thì sang Ollama GPU (<1s)!

                if _finalize_partial_stream():
                    return
                _GEMINI_COOLDOWN_UNTIL[model_name] = time.monotonic() + 10.0
                break
            except Exception as e:
                last_err = e
                if _finalize_partial_stream():
                    return
                abort_all_models = True
                break

        if abort_all_models:
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


def _ask_ai(
    raw_message: str,
    text_norm: str,
    prefs: dict | None = None,
    mem_events: dict | None = None,
) -> str:
    """Ưu tiên gọi Google AI Studio (Gemini) khi có mạng; tự động fallback về Ollama local khi offline."""
    system_content = _build_system_context(text_norm, prefs, raw_message=raw_message)
    online_reply = _ask_gemini(raw_message, system_content, prefs=prefs)
    if online_reply:
        _schedule_background_memory_learning(raw_message, online_reply)
        return online_reply

    local_reply = _ask_ollama(raw_message, text_norm, prebuilt_system=system_content, prefs=prefs)
    if local_reply and local_reply != OLLAMA_WAITING_MSG:
        _schedule_background_memory_learning(raw_message, local_reply)
        return local_reply

    offline_mem_reply = _answer_personal_memory_offline(raw_message, text_norm, mem_events=mem_events)
    if offline_mem_reply:
        cleaned = _clean_reply_by_prefs(offline_mem_reply, prefs, is_fast_path=True)
        _append_chat_history(raw_message, cleaned, is_fast_path=True)
        return cleaned
    return local_reply


def get_reply(message: str, prefs: dict | None = None) -> str:
    raw = normalize_vietnamese_input(message)
    if not raw:
        return "Gõ gì đó đi, vd: `thời tiết`, `mấy giờ` 😄"
    n = _norm(raw)
    core = _strip_conversational_fillers(n)

    # Tự động học thông tin Hồ sơ cá nhân nếu người dùng giới thiệu trong câu chat
    extract_and_update_profile_from_message(raw)

    # --- Xóa lịch sử hội thoại (giữ nguyên Trí nhớ dài hạn, Việc cần nhớ & Hồ sơ) ---
    if core in ("xoa lich su", "clear", "reset", "xoa chat", "xoa hoi thoai"):
        _clear_chat_history()
        return _clean_reply_by_prefs("🧹 Wisp đã xóa lịch sử trò chuyện.", prefs, is_fast_path=True)

    # --- Tầng 1 (<1ms): Bắt tức thì lệnh Nhắc việc / Hoàn thành việc / Quên ký ức / Ghi nhớ ---
    mem_events = extract_instant_memory_and_tasks(raw)
    if mem_events.get("direct_reply"):
        cleaned = _clean_reply_by_prefs(str(mem_events["direct_reply"]), prefs, is_fast_path=True)
        _append_chat_history(raw, cleaned, is_fast_path=True)
        return cleaned

    # --- Thời tiết tra cứu ngắn (hỗ trợ hiện tại, ngày mai, ngày mốt, cuối tuần, 7 ngày tới) ---
    if _is_short_weather_command(n):
        city = _extract_city_after(n, ["thoi tiet", "weather", "nhiet do", "du bao"])
        if not city:
            city = _detect_city_in_text(n)
        res = _get_weather_for_query(city, raw, n)
        cleaned = _clean_reply_by_prefs(res, prefs, is_fast_path=True)
        _append_chat_history(raw, cleaned, is_fast_path=True)
        return cleaned

    # --- Các lệnh tra cứu nhanh (Help, Chào, Giờ, Ngày, Cảm ơn, Tạm biệt) ---
    fast_reply = _match_fast_command(n)
    if fast_reply is not None:
        cleaned = _clean_reply_by_prefs(fast_reply, prefs, is_fast_path=True)
        _append_chat_history(raw, cleaned, is_fast_path=True)
        return cleaned

    # --- Mọi câu hỏi tự nhiên, tư vấn chủ động, tra cứu Web, viết code -> Gemini Online / Ollama ---
    return _ask_ai(raw, n, prefs=prefs, mem_events=mem_events)


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

    # Tự động học thông tin Hồ sơ cá nhân nếu người dùng chia sẻ trong câu chat
    learned_fields = extract_and_update_profile_from_message(raw)
    if learned_fields:
        yield {
            "type": "profile_updated",
            "profile": get_user_profile(),
            "learned": learned_fields,
        }

    # Xóa bộ nhớ hội thoại -> chỉ xóa khung chat hiện tại + tóm tắt phiên, bảo toàn 100% Ký ức dài hạn & Việc cần nhớ
    if core in ("xoa lich su", "clear", "reset", "xoa chat", "xoa hoi thoai"):
        yield {"type": "state", "state": "shaping", "label": "Đang làm mới bộ nhớ hội thoại…"}
        _clear_chat_history()
        cleaned = _clean_reply_by_prefs("🧹 Wisp đã xóa lịch sử trò chuyện.", p, is_fast_path=True)
        yield {"type": "token", "delta": cleaned}
        yield {"type": "done", "reply": cleaned}
        return

    # Tầng 1 (<1ms): Bắt nhanh Ký ức & Việc cần nhớ và phát sự kiện SSE memory_updated cho UI
    mem_events = extract_instant_memory_and_tasks(raw)
    has_mem_change = bool(
        mem_events["memories_added"]
        or mem_events["memories_forgotten"]
        or mem_events["tasks_added"]
        or mem_events["tasks_completed"]
    )
    if has_mem_change:
        yield {
            "type": "memory_updated",
            "toast": mem_events["toast"],
            "memories_added": mem_events["memories_added"],
            "memories_forgotten": mem_events["memories_forgotten"],
            "tasks_added": mem_events["tasks_added"],
            "tasks_completed": mem_events["tasks_completed"],
            "counts": memory_store.get_counts(),
        }

    if mem_events.get("direct_reply"):
        direct_txt = _clean_reply_by_prefs(str(mem_events["direct_reply"]), p, is_fast_path=True)
        _append_chat_history(raw, direct_txt, is_fast_path=True)
        yield {"type": "state", "state": "composing", "label": "Đang phản hồi…"}
        yield {"type": "token", "delta": direct_txt}
        yield {"type": "done", "reply": direct_txt}
        return

    # Fast-path: Thời tiết ngắn (hiện tại / ngày mai / ngày mốt / cuối tuần / N ngày tới)
    if _is_short_weather_command(n):
        city = _extract_city_after(n, ["thoi tiet", "weather", "nhiet do", "du bao"]) or _detect_city_in_text(n)
        yield {"type": "state", "state": "searching", "label": "Đang truy xuất dữ liệu Open-Meteo…"}
        res = _get_weather_for_query(city, raw, n)
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

    # Hợp nhất ngữ cảnh chủ động (Hồ sơ + Ký ức SQLite + Việc cần nhớ + Tóm tắt phiên + Thời tiết 1-7 ngày)
    now = datetime.now(VN_TZ)
    preloaded_weather: str | None = None
    if _is_weather_topic(raw, n):
        yield {"type": "state", "state": "searching", "label": "Đang lấy dữ liệu thời tiết thực tế…"}
        city = _detect_city_in_text(n)
        preloaded_weather = _get_weather_for_query(city, raw, n, now_dt=now)
        yield {"type": "state", "state": "weaving", "label": "Đang đan ghép dữ liệu vào ngữ cảnh AI…"}

    system_content = _build_system_context(
        n,
        p,
        raw_message=raw,
        is_voice=is_voice,
        preloaded_weather=preloaded_weather,
    )

    reasoning_plan = _classify_reasoning_plan(raw, n, prefs=p, is_voice=is_voice)
    use_web = _needs_web_search_grounding(raw, n)

    # 1. ƯU TIÊN ONLINE: Thử stream qua Google AI Studio (Gemini API) nếu mạch mạng đang mở và có model chưa bị cooldown
    now_mono = time.monotonic()
    if (
        GEMINI_API_KEY
        and now_mono >= _GEMINI_NETWORK_OFFLINE_UNTIL
        and any(now_mono >= _GEMINI_COOLDOWN_UNTIL.get(m, 0.0) for m in GEMINI_MODELS)
    ):
        try:
            if use_web and now_mono >= _GEMINI_WEB_SEARCH_COOLDOWN_UNTIL:
                yield {"type": "state", "state": "searching", "label": "Đang tra cứu Web thời gian thực…"}
            solving_label = (
                "Đang suy luận chuyên sâu…"
                if reasoning_plan["thinking_budget"] > 0
                else "Đang suy luận qua Google AI Studio…"
            )
            yield {"type": "state", "state": "solving", "label": solving_label}
            streamed_parts: list[str] = []
            for delta in _stream_gemini_tokens(
                raw,
                system_content,
                prefs=p,
                is_voice=is_voice,
                reasoning_plan=reasoning_plan,
                use_web_search=use_web,
            ):
                streamed_parts.append(delta)
                yield {"type": "token", "delta": delta}
            if streamed_parts:
                final_clean = _clean_reply_by_prefs("".join(streamed_parts).strip(), p, is_fast_path=False)
                if final_clean:
                    _schedule_background_memory_learning(raw, final_clean)
                    yield {"type": "done", "reply": final_clean}
                    return
        except Exception:
            # Mất mạng hoặc Gemini bận -> chuyển mượt sang Ollama local bên dưới
            pass

    # 2. FALLBACK OFFLINE: Luồng AI Local (Ollama Streaming từ GPU)
    try:
        model = _resolve_ollama_model()
        if not model:
            offline_mem_reply = _answer_personal_memory_offline(raw, n, mem_events=mem_events, now_dt=now)
            if offline_mem_reply:
                clean_off = _clean_reply_by_prefs(offline_mem_reply, p, is_fast_path=True)
                _append_chat_history(raw, clean_off, is_fast_path=True)
                yield {"type": "token", "delta": clean_off}
                yield {"type": "done", "reply": clean_off}
                return
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
            offline_mem_reply = _answer_personal_memory_offline(raw, n, mem_events=mem_events, now_dt=now)
            if offline_mem_reply:
                clean_off = _clean_reply_by_prefs(offline_mem_reply, p, is_fast_path=True)
                _append_chat_history(raw, clean_off, is_fast_path=True)
                yield {"type": "token", "delta": clean_off}
                yield {"type": "done", "reply": clean_off}
            else:
                yield {"type": "token", "delta": OLLAMA_WAITING_MSG}
                yield {"type": "done"}
        else:
            _append_chat_history(raw, full_reply)
            _schedule_background_memory_learning(raw, full_reply)
            yield {"type": "done", "reply": full_reply}
    except Exception:
        offline_mem_reply = _answer_personal_memory_offline(raw, n, mem_events=mem_events, now_dt=now)
        if offline_mem_reply:
            clean_off = _clean_reply_by_prefs(offline_mem_reply, p, is_fast_path=True)
            _append_chat_history(raw, clean_off, is_fast_path=True)
            yield {"type": "token", "delta": clean_off}
            yield {"type": "done", "reply": clean_off}
        else:
            yield {"type": "token", "delta": OLLAMA_WAITING_MSG}
            yield {"type": "done"}






