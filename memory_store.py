"""
Lưu trữ & Truy xuất Trí nhớ Dài hạn (SQLite cục bộ — stdlib 100%).
Quản lý 3 bảng cốt lõi trong wisp_memory.db:
- memories: Ký ức dài hạn phân loại (preference, project, skill, habit, health, relationship, fact)
- tasks: Việc cần nhớ / Lời nhắc / Kế hoạch cá nhân (pending, done)
- episodic_summaries: Bản tóm tắt cuộn cho phiên hội thoại dài (> 10 lượt)
"""
from __future__ import annotations

import atexit
import math
import os
import re
import sqlite3
import sys
import tempfile
import threading
import unicodedata
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

try:
    VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
except Exception:
    VN_TZ = timezone(timedelta(hours=7))

WEEKDAY_VI = [
    "Thứ Hai",
    "Thứ Ba",
    "Thứ Tư",
    "Thứ Năm",
    "Thứ Sáu",
    "Thứ Bảy",
    "Chủ Nhật",
]

VALID_MEMORY_CATEGORIES = (
    "preference",
    "project",
    "skill",
    "habit",
    "health",
    "relationship",
    "fact",
)

MAX_ACTIVE_MEMORIES = 300

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
PROD_DB_PATH = os.path.join(ROOT_DIR, "wisp_memory.db")


def _cleanup_temp_db_files(base_path: str) -> None:
    for suffix in ("", "-wal", "-shm"):
        p = base_path + suffix
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass


def _resolve_initial_db_path() -> str:
    env_override = (os.environ.get("WISP_MEMORY_DB") or "").strip()
    if env_override:
        return env_override
    # Khi chạy unittest/pytest, tự động cô lập sang file tạm để tuyệt đối không chạm vào DB thật của người dùng
    if (
        "unittest" in sys.modules
        or "pytest" in sys.modules
        or any(
            "unittest" in os.path.basename(str(arg)).lower()
            or "pytest" in os.path.basename(str(arg)).lower()
            for arg in sys.argv
        )
    ):
        tmp_path = os.path.join(
            tempfile.gettempdir(),
            f"wisp_unittest_memory_{os.getpid()}.db",
        )
        atexit.register(_cleanup_temp_db_files, tmp_path)
        return tmp_path
    return PROD_DB_PATH


def strip_accents(text: str) -> str:
    """Chuyển chuỗi tiếng Việt có dấu sang không dấu chữ thường để tìm kiếm song song có/không dấu."""
    if not text:
        return ""
    s = str(text).replace("Đ", "D").replace("đ", "d").replace("Ð", "D").replace("ð", "d")
    s = "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"\s+", " ", s.lower()).strip()


# Các cụm từ khung tiếng Việt có dấu cần lược bỏ trước khi trích token đặc trưng (tránh gộp nhầm 2 dự án/sở thích khác nhau)
_ACCENTED_FRAMING_PHRASES = (
    r"\bdự\s+án\b",
    r"\bứng\s+dụng\b",
    r"\btên\s+là\b",
    r"\bcó\s+tên\b",
    r"\bthói\s+quen\b",
    r"\bsở\s+thích\b",
    r"\byêu\s+thích\b",
    r"\bkhông\s+thích\b",
    r"\bdị\s+ứng\s+với\b",
    r"\bdị\s+ứng\b",
    r"\bghi\s+nhớ\b",
    r"\blàm\s+việc\b",
    r"\bchuyển\s+sang\b",
)

_NORM_STOPWORDS = frozenset({
    "minh", "toi", "to", "tui", "anh", "em", "ban", "chu", "nhan", "wisp",
    "la", "dang", "da", "se", "vua", "moi", "sap", "van", "con", "rat", "hoi",
    "kha", "cuc", "ky", "nhat", "hon", "cua", "va", "voi", "cho", "trong",
    "ngoai", "tren", "duoi", "o", "tai", "tu", "den", "vao", "khi", "luc", "neu",
    "thi", "ma", "nhung", "hoac", "hay", "thuong", "thich", "ghet", "me",
    "chuong", "ngai", "lam", "dung", "xai", "hoc", "viet", "phat", "trien",
    "xay", "ten", "mot", "cac", "nay", "kia", "do", "day",
    "nhe", "nha", "ne", "a", "roi", "di", "duoc", "khong", "chua", "co",
    "bi", "ve", "theo", "giup", "project", "app",
})

_FORGET_QUERY_STOPWORDS = frozenset({
    "quen", "xoa", "bo", "dung", "nho", "chuyen", "viec", "thong", "tin",
    "ky", "uc", "dieu", "rang", "la", "ve", "giup", "di", "luu",
})

_OBJECT_MODIFIER_TOKENS = frozenset({
    "buoi", "sang", "trua", "chieu", "toi", "dem", "khuya", "ngay", "tuan",
    "thang", "nam", "it", "nhieu", "nhe", "dam", "nong", "lanh", "am", "mat",
})

_GENERIC_ENTITY_SKIP = frozenset({
    "minh", "toi", "anh", "em", "ban", "wisp", "project", "app", "web", "mobile",
    "du", "an", "ung", "dung", "thich", "khong", "dang", "lam", "ghi", "nho",
})


_NORM_FRAMING_PHRASES = (
    r"\bdu\s+an\b",
    r"\bung\s+dung\b",
    r"\bten\s+la\b",
    r"\bco\s+ten\b",
    r"\bthoi\s+quen\b",
    r"\bso\s+thich\b",
    r"\byeu\s+thich\b",
    r"\bkhong\s+thich\b",
    r"\bdi\s+ung\s+voi\b",
    r"\bdi\s+ung\b",
    r"\bghi\s+nho\b",
    r"\blam\s+viec\b",
    r"\bchuyen\s+sang\b",
)


def extract_informative_tokens(text: str) -> list[str]:
    """Trích xuất danh sách token mang nghĩa đặc trưng từ câu tiếng Việt (có dấu hoặc không dấu)."""
    if not text:
        return []
    cleaned = str(text).lower()
    for pat in _ACCENTED_FRAMING_PHRASES:
        cleaned = re.sub(pat, " ", cleaned, flags=re.IGNORECASE)
    norm = strip_accents(cleaned)
    for pat in _NORM_FRAMING_PHRASES:
        norm = re.sub(pat, " ", norm, flags=re.IGNORECASE)
    raw_tokens = re.findall(r"[a-z0-9_+#.-]+", norm)
    out: list[str] = []
    for tok in raw_tokens:
        t = tok.strip("._-")
        if not t or len(t) < 2:
            continue
        if t in _NORM_STOPWORDS:
            continue
        out.append(t)
    if not out:
        # Dự phòng cho câu cực ngắn toàn từ thông dụng
        out = [t for t in re.findall(r"[a-z0-9]+", strip_accents(text)) if len(t) >= 2]
    return out


def _has_replacement_cue(text: str) -> bool:
    """Kiểm tra câu mới có mang tín hiệu cập nhật / thay đổi thông tin cũ hay không (VD: 'chuyển sang', 'đổi sang', 'không còn')."""
    n = strip_accents(text)
    return bool(
        re.search(
            r"\b(chuyen sang|doi sang|doi thanh|thay vi|thay cho|thay bang|khong con|het bi|bo han|ngung|cap nhat|chuyen tu)\b",
            n,
        )
    )


def _has_negation_flip(old_text: str, new_text: str) -> bool:
    """Kiểm tra câu mới có đảo trạng thái phủ định so với câu cũ không (VD: từ thích sang không thích)."""
    neg_re = r"\b(khong|het|bo|ngung|kieng|tranh)\b"
    old_neg = bool(re.search(neg_re, strip_accents(old_text)))
    new_neg = bool(re.search(neg_re, strip_accents(new_text)))
    return old_neg != new_neg


def _extract_proper_entities(text: str) -> set[str]:
    """Trích xuất tên thực thể riêng (CamelCase, chữ viết hoa tên riêng, mã chữ+số, từ sau 'tên/dự án/app' hoặc trong ngoặc kép)."""
    if not text:
        return set()
    raw = str(text).strip()
    entities: set[str] = set()

    # 1. CamelCase / PascalCase nội bộ (FitNote, SwiftUI, iOS, ExpensePro), chữ IN HOA >= 2 ký tự (API, AWS), hoặc chữ+số (Riverpod2, M4)
    for tok in re.findall(r"[A-Za-zÀ-ỹ0-9_+#-]+", raw):
        clean_tok = tok.strip("_-")
        if len(clean_tok) < 2:
            continue
        has_internal_upper = any(c.isupper() for c in clean_tok[1:])
        is_all_upper = clean_tok.isupper() and any(c.isalpha() for c in clean_tok)
        has_alpha_and_digit = any(c.isalpha() for c in clean_tok) and any(c.isdigit() for c in clean_tok)
        if has_internal_upper or is_all_upper or has_alpha_and_digit:
            n_tok = strip_accents(clean_tok)
            if n_tok not in _GENERIC_ENTITY_SKIP:
                entities.add(n_tok)

    # 2. Thực thể đặt trong dấu ngoặc kép / nháy đơn
    for q_match in re.finditer(r"[\"'“”‘’]([A-Za-zÀ-ỹ0-9_+#.-]{2,40})[\"'“”‘’]", raw):
        n_tok = strip_accents(q_match.group(1)).strip("._-")
        if len(n_tok) >= 2 and n_tok not in _GENERIC_ENTITY_SKIP:
            entities.add(n_tok)

    # 3. Định danh ngay sau 'tên', 'tên là', 'có tên', 'gọi là'
    for named_m in re.finditer(
        r"\b(?:tên(?:\s+là)?|có\s+tên(?:\s+là)?|gọi\s+là|ten(?:\s+la)?|co\s+ten(?:\s+la)?|goi\s+la)\s+([A-Za-zÀ-ỹ0-9_+#.-]{2,40})",
        raw,
        flags=re.IGNORECASE,
    ):
        n_tok = strip_accents(named_m.group(1)).strip("._-")
        if len(n_tok) >= 2 and n_tok not in _GENERIC_ENTITY_SKIP and n_tok not in _NORM_STOPWORDS:
            entities.add(n_tok)

    return entities


def _extract_primary_object_tokens(text: str) -> set[str]:
    """Trích xuất danh từ đối tượng chính ngay sau động từ sở thích/ăn uống/dị ứng (VD: 'ăn phở bò' -> {'pho', 'bo'})."""
    norm = strip_accents(text)
    if not norm:
        return set()
    objs: set[str] = set()
    for m in re.finditer(
        r"\b(?:an|uong|di\s+ung|kieng|thich|ghet|me|choi|nuoi|trong|nghe|xem|doc)\s+"
        r"(?:duoc\s+|voi\s+|mon\s+|do\s+|nuoc\s+|con\s+|bo\s+mon\s+)?"
        r"([a-z0-9]+)(?:\s+([a-z0-9]+))?",
        norm,
    ):
        for grp in (m.group(1), m.group(2)):
            if not grp or len(grp) < 2:
                continue
            if grp in _NORM_STOPWORDS or grp in _OBJECT_MODIFIER_TOKENS:
                continue
            if grp in ("an", "uong", "choi", "hoc", "lam", "dung", "tap", "chay", "di"):
                continue
            objs.add(grp)
    return objs


def _has_conflicting_subject_or_entity(
    old_content: str,
    new_content: str,
    old_tokens: set[str],
    new_tokens: set[str],
) -> bool:
    """
    Ngăn việc gộp đè hai ký ức độc lập có chung khung câu (VD: 2 dự án SwiftUI tên FitNote vs ExpensePro,
    hoặc 2 món ăn sáng 'phở bò tái' vs 'bún bò tái') khi người dùng KHÔNG nêu ý thay thế/chuyển đổi.
    """
    if _has_replacement_cue(new_content):
        return False

    old_ents = _extract_proper_entities(old_content)
    new_ents = _extract_proper_entities(new_content)
    if (old_ents - new_tokens) and (new_ents - old_tokens):
        return True

    old_objs = _extract_primary_object_tokens(old_content)
    new_objs = _extract_primary_object_tokens(new_content)
    if (old_objs - new_tokens) and (new_objs - old_tokens):
        return True

    return False


def infer_memory_category(text: str) -> str:
    """Tự động phân loại nhóm ký ức từ nội dung tiếng Việt."""
    raw = (text or "").lower()
    norm = strip_accents(raw)

    if re.search(r"\b(dự án|project|ứng dụng|app|testflight|repo|sản phẩm|khởi nghiệp)\b", raw) or re.search(
        r"\b(du an|project|testflight|xay dung app|lam app)\b", norm
    ):
        return "project"
    if re.search(
        r"\b(dị ứng|bệnh|thuốc|đau dạ dày|mất ngủ|huyết áp|tiểu đường|sức khỏe|kiêng|không uống được|không ăn được)\b",
        raw,
    ) or re.search(r"\b(di ung|dau da day|mat ngu|suc khoe|khong uong duoc|khong an duoc)\b", norm):
        return "health"
    if re.search(r"\b(vợ|chồng|người yêu|con gái|con trai|bố|mẹ|gia đình|bạn thân|đồng nghiệp|sếp)\b", raw):
        return "relationship"
    if re.search(
        r"\b(swift|swiftui|uikit|flutter|dart|python|react|typescript|rust|golang|sql|docker|kiến trúc|lập trình|riverpod|bloc)\b",
        norm,
    ) and re.search(r"\b(chuyen|hoc|thanh thao|kinh nghiem|ky nang|dung|stack)\b", norm):
        return "skill"
    if re.search(r"\b(thói quen|thường xuyên|mỗi sáng|mỗi tối|hàng ngày|hay thức khuya|chạy bộ|tập gym)\b", raw) or re.search(
        r"\b(thoi quen|moi sang|moi toi|hang ngay|thuc khuya|chay bo|tap gym)\b", norm
    ):
        return "habit"
    if re.search(r"\b(thích|ghét|mê|chuộng|ưu tiên|yêu thích|không thích|khoái)\b", raw) or re.search(
        r"\b(thich|ghet|uu tien|yeu thich|khong thich)\b", norm
    ):
        return "preference"
    return "fact"


def _dice_similarity(tokens_a: set[str], tokens_b: set[str]) -> float:
    if not tokens_a or not tokens_b:
        return 0.0
    inter = len(tokens_a & tokens_b)
    if inter == 0:
        return 0.0
    return (2.0 * inter) / (len(tokens_a) + len(tokens_b))


def _jaccard_similarity(tokens_a: set[str], tokens_b: set[str]) -> float:
    if not tokens_a or not tokens_b:
        return 0.0
    union = len(tokens_a | tokens_b)
    if union == 0:
        return 0.0
    return len(tokens_a & tokens_b) / union


def parse_vietnamese_due_time(text: str, now_dt: datetime | None = None) -> tuple[str, str]:
    """
    Phân tích mốc thời gian tự nhiên tiếng Việt (có dấu & không dấu) sang (due_text, due_iso).
    Hỗ trợ: "chiều nay", "sáng mai 9h", "ngày mốt", "thứ Hai tuần sau", "cuối tuần này",
    "15h30 ngày 5/11", "3 ngày nữa", "2 tiếng nữa"...
    """
    if not text or not str(text).strip():
        return ("", "")

    now = now_dt or datetime.now(VN_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=VN_TZ)

    raw = str(text).strip()
    norm = strip_accents(raw)

    # 1. Tương đối theo giờ / phút ("2 tiếng nữa", "30 phút nữa")
    m_rel_hm = re.search(r"\b(\d{1,3})\s*(tieng|gio|phut)\s+nua\b", norm)
    if m_rel_hm:
        val = int(m_rel_hm.group(1))
        unit = m_rel_hm.group(2)
        target_dt = now + (timedelta(minutes=val) if unit == "phut" else timedelta(hours=val))
        due_iso = target_dt.strftime("%Y-%m-%dT%H:%M")
        due_text = f"{WEEKDAY_VI[target_dt.weekday()]}, {target_dt.strftime('%d/%m/%Y %H:%M')}"
        return (due_text, due_iso)

    # 2. Tách buổi trong ngày (sáng / trưa / chiều / tối / đêm)
    period: str | None = None
    is_midnight_phrase = bool(re.search(r"\bnua\s+dem\b", norm))
    if re.search(r"\b(sang|buoi sang)\b", norm):
        period = "morning"
    elif re.search(r"\b(trua|buoi trua)\b", norm):
        period = "noon"
    elif re.search(r"\b(chieu|buoi chieu)\b", norm):
        period = "afternoon"
    elif re.search(r"\b(toi|buoi toi)\b", norm):
        period = "evening"
    elif re.search(r"\b(dem|khuya|nua dem)\b", norm):
        period = "night"

    # 3. Tách giờ:phút cụ thể (VD: "15h30", "9h", "15:30", "9 giờ 15", "9 giờ")
    parsed_hour: int | None = None
    parsed_min: int = 0
    has_explicit_clock = False

    m_hm_colon = re.search(r"(?<![/\d])\b([01]?\d|2[0-3])\s*[:h]\s*([0-5]\d)\b", norm)
    if m_hm_colon:
        parsed_hour = int(m_hm_colon.group(1))
        parsed_min = int(m_hm_colon.group(2))
        has_explicit_clock = True
    else:
        m_gio_min = re.search(r"\b([01]?\d|2[0-3])\s*(?:h|gio)\s*(?:([0-5]?\d)\s*(?:phut|p)?|(ruoi))?\b", norm)
        if m_gio_min:
            # Tránh bắt nhầm "24 giờ qua"
            after_span = norm[m_gio_min.end() : m_gio_min.end() + 10]
            if not re.match(r"^\s*(qua|truoc)\b", after_span):
                parsed_hour = int(m_gio_min.group(1))
                has_explicit_clock = True
                if m_gio_min.group(3):
                    parsed_min = 30
                elif m_gio_min.group(2):
                    parsed_min = int(m_gio_min.group(2))

    if parsed_hour is not None:
        if period in ("afternoon", "evening") and 1 <= parsed_hour <= 11:
            parsed_hour += 12
        elif period == "night":
            if 6 <= parsed_hour <= 11:
                parsed_hour += 12
            elif parsed_hour == 12:
                parsed_hour = 0
        elif period == "noon" and parsed_hour == 0:
            parsed_hour = 12
    elif is_midnight_phrase:
        parsed_hour = 0
        parsed_min = 0
        has_explicit_clock = True
    elif period is not None:
        default_period_hours = {
            "morning": 8,
            "noon": 12,
            "afternoon": 15,
            "evening": 20,
            "night": 22,
        }
        parsed_hour = default_period_hours[period]
        parsed_min = 0

    # 4. Tách ngày mục tiêu
    target_date: date | None = None
    has_explicit_rel_day = bool(
        re.search(
            r"\b(\d{1,2}\s*ngay\s+(?:nua|toi)|sau\s+\d{1,2}\s*ngay|"
            r"ngay mot|ngay kia|sang mot|trua mot|chieu mot|toi mot|"
            r"ngay mai|sang mai|trua mai|chieu mai|toi mai|dem mai|tomorrow|"
            r"hom nay|sang nay|trua nay|chieu nay|toi nay|dem nay|khuya nay|nua dem nay|today|"
            r"cuoi\s+tuan(?:\s+(?:nay|sau|toi))?|"
            r"thu\s*(?:hai|ba|tu|bon|nam|sau|bay|[2-7])|chu\s*nhat|tuan\s+(?:sau|toi))\b",
            norm,
        )
    )

    # 4a. Ngày/tháng tường minh: "ngày 5/11", "05/11/2026", hoặc "5/11" (khi không phải phân số/chương mục như 'chương 3/4 tối nay')
    m_dmy_explicit = re.search(
        r"(?:\bngay\s+|\bmung\s+|\bhom\s+|\bhan\s+|\bvao\s+ngay\s+)(\d{1,2})[/-](\d{1,2})(?:[/-](20\d{2}))?\b"
        r"|\b(\d{1,2})[/-](\d{1,2})[/-](20\d{2})\b",
        norm,
    )
    dd: int | None = None
    mm: int | None = None
    yy_s: str | None = None
    if m_dmy_explicit:
        if m_dmy_explicit.group(1) and m_dmy_explicit.group(2):
            dd, mm, yy_s = int(m_dmy_explicit.group(1)), int(m_dmy_explicit.group(2)), m_dmy_explicit.group(3)
        else:
            dd, mm, yy_s = int(m_dmy_explicit.group(4)), int(m_dmy_explicit.group(5)), m_dmy_explicit.group(6)
    elif not has_explicit_rel_day:
        for m_bare in re.finditer(r"\b(\d{1,2})/(\d{1,2})\b", norm):
            before_bare = norm[: m_bare.start()]
            if re.search(
                r"\b(?:chuong|phan|bai|tap|muc|trang|cau|ty\s+le|ti\s+le|phan\s+so|lop|tang|ban|v|ver|version|buoc|step|pr|issue)\s*$",
                before_bare,
            ):
                continue
            dd, mm, yy_s = int(m_bare.group(1)), int(m_bare.group(2)), None
            break

    if dd is not None and mm is not None and 1 <= mm <= 12 and 1 <= dd <= 31:
        yr = int(yy_s) if yy_s else now.year
        try:
            cand = date(yr, mm, dd)
            if not yy_s and (now.date() - cand).days > 30:
                cand = date(yr + 1, mm, dd)
            target_date = cand
        except ValueError:
            target_date = None

    # 4b. Mốc ngày tương đối bằng chữ tiếng Việt
    if target_date is None:
        m_days_ahead = re.search(r"\b(\d{1,2})\s*ngay\s+(?:nua|toi)\b|\bsau\s+(\d{1,2})\s*ngay\b", norm)
        if m_days_ahead:
            n_days = int(m_days_ahead.group(1) or m_days_ahead.group(2))
            target_date = now.date() + timedelta(days=n_days)
        elif re.search(r"\b(ngay mot|ngay kia|sang mot|trua mot|chieu mot|toi mot)\b", norm):
            target_date = now.date() + timedelta(days=2)
        elif re.search(r"\b(ngay mai|sang mai|trua mai|chieu mai|toi mai|dem mai|tomorrow)\b", norm) or re.search(
            r"(?:^|\s)mai\s+(?:luc\s+)?\d{1,2}\s*(?:h|gio|:)", norm
        ):
            target_date = now.date() + timedelta(days=1)
        elif re.search(r"\b(hom nay|sang nay|trua nay|chieu nay|toi nay|dem nay|khuya nay|nua dem nay|today)\b", norm):
            target_date = now.date()
            # "12h đêm nay" / "nửa đêm nay" nói vào ban ngày/tối là 00:00 rạng sáng hôm sau
            if parsed_hour == 0 and period == "night" and now.hour >= 5:
                target_date = now.date() + timedelta(days=1)
        elif re.search(r"\bcuoi\s+tuan\s+(?:sau|toi)\b", norm):
            days_to_next_mon = 7 - now.weekday()
            target_date = now.date() + timedelta(days=days_to_next_mon + 5)
        elif re.search(r"\bcuoi\s+tuan(?:\s+nay)?\b", norm):
            if now.weekday() <= 5:
                target_date = now.date() + timedelta(days=5 - now.weekday())
            else:
                target_date = now.date()
        else:
            # Thứ trong tuần: "thứ Hai", "thứ 2", ..., "Chủ Nhật"
            weekday_map = [
                (r"\bthu\s*(?:hai|2)\b", 0),
                (r"\bthu\s*(?:ba|3)\b", 1),
                (r"\bthu\s*(?:tu|bon|4)\b", 2),
                (r"\bthu\s*(?:nam|5)\b", 3),
                (r"\bthu\s*(?:sau|6)\b", 4),
                (r"\bthu\s*(?:bay|7)\b", 5),
                (r"\b(?:chu\s*nhat|cn)\b", 6),
            ]
            matched_wd: int | None = None
            for pat, wd_idx in weekday_map:
                if re.search(pat, norm):
                    matched_wd = wd_idx
                    break
            if matched_wd is not None:
                is_next_week = bool(re.search(r"\btuan\s+(?:sau|toi)\b", norm))
                cur_wd = now.weekday()
                if is_next_week:
                    days_to_next_monday = 7 - cur_wd
                    target_date = now.date() + timedelta(days=days_to_next_monday + matched_wd)
                else:
                    delta = (matched_wd - cur_wd) % 7
                    if delta == 0 and parsed_hour is not None:
                        cand_dt = datetime(
                            now.year, now.month, now.day, parsed_hour, parsed_min, tzinfo=now.tzinfo
                        )
                        if cand_dt <= now and not re.search(r"\b(nay|hom nay)\b", norm):
                            delta = 7
                    target_date = now.date() + timedelta(days=delta)
            elif re.search(r"\btuan\s+(?:sau|toi)\b", norm):
                days_to_next_monday = 7 - now.weekday()
                target_date = now.date() + timedelta(days=days_to_next_monday)

    # Nếu chỉ có giờ hoặc buổi cụ thể (không phải thói quen lặp lại "mỗi tối") mà chưa nêu ngày
    if target_date is None and parsed_hour is not None:
        is_recurring_habit = bool(
            re.search(r"\b(moi|hang|thuong)\s+(sang|trua|chieu|toi|dem|ngay)\b", norm)
        )
        if has_explicit_clock or (period is not None and not is_recurring_habit):
            cand_dt = datetime(now.year, now.month, now.day, parsed_hour, parsed_min, tzinfo=now.tzinfo)
            target_date = now.date() if cand_dt > now else (now.date() + timedelta(days=1))

    if target_date is None:
        return ("", "")

    wd_label = WEEKDAY_VI[target_date.weekday()]
    date_vi = target_date.strftime("%d/%m/%Y")
    date_iso = target_date.strftime("%Y-%m-%d")
    if parsed_hour is not None:
        due_text = f"{wd_label}, {date_vi} {parsed_hour:02d}:{parsed_min:02d}"
        due_iso = f"{date_iso}T{parsed_hour:02d}:{parsed_min:02d}"
    else:
        due_text = f"{wd_label}, {date_vi}"
        due_iso = date_iso
    return (due_text, due_iso)


class MemoryStore:
    """Kho lưu trữ SQLite an toàn đa luồng (WAL + RLock + đóng handle sạch trên Windows)."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or _resolve_initial_db_path()
        self._lock = threading.RLock()
        self._initialized = False
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        parent = os.path.dirname(os.path.abspath(self.db_path))
        if parent and not os.path.exists(parent):
            os.makedirs(parent, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._lock:
            if self._initialized and os.path.exists(self.db_path):
                return
            conn = self._connect()
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=NORMAL;")
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS memories (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        category TEXT NOT NULL DEFAULT 'fact',
                        content TEXT NOT NULL,
                        norm_text TEXT NOT NULL,
                        importance INTEGER NOT NULL DEFAULT 3,
                        pinned INTEGER NOT NULL DEFAULT 0,
                        source TEXT NOT NULL DEFAULT 'auto',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        last_used_at TEXT DEFAULT ''
                    );

                    CREATE INDEX IF NOT EXISTS idx_memories_pinned_updated
                        ON memories(pinned DESC, importance DESC, updated_at DESC);

                    CREATE TABLE IF NOT EXISTS tasks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        title TEXT NOT NULL,
                        norm_title TEXT NOT NULL,
                        due_text TEXT NOT NULL DEFAULT '',
                        due_iso TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL DEFAULT 'pending',
                        priority INTEGER NOT NULL DEFAULT 2,
                        created_at TEXT NOT NULL,
                        completed_at TEXT NOT NULL DEFAULT ''
                    );

                    CREATE INDEX IF NOT EXISTS idx_tasks_status_due
                        ON tasks(status, due_iso);

                    CREATE TABLE IF NOT EXISTS episodic_summaries (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        summary TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    """
                )
                conn.commit()
                self._initialized = True
            finally:
                conn.close()

    @staticmethod
    def _now_iso(now_dt: datetime | None = None) -> str:
        dt = now_dt or datetime.now(VN_TZ)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=VN_TZ)
        return dt.isoformat(timespec="seconds")

    @staticmethod
    def _row_to_memory(row: sqlite3.Row) -> dict:
        return {
            "id": int(row["id"]),
            "category": str(row["category"]),
            "content": str(row["content"]),
            "norm_text": str(row["norm_text"]),
            "importance": int(row["importance"]),
            "pinned": bool(row["pinned"]),
            "source": str(row["source"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "last_used_at": str(row["last_used_at"] or ""),
        }

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> dict:
        return {
            "id": int(row["id"]),
            "title": str(row["title"]),
            "norm_title": str(row["norm_title"]),
            "due_text": str(row["due_text"] or ""),
            "due_iso": str(row["due_iso"] or ""),
            "status": str(row["status"]),
            "priority": int(row["priority"]),
            "created_at": str(row["created_at"]),
            "completed_at": str(row["completed_at"] or ""),
        }

    # ------------------------------------------------------------------
    # MEMORIES CRUD & SMART DEDUP / UPSERT
    # ------------------------------------------------------------------
    def upsert_memory(
        self,
        content: str,
        category: str = "auto",
        importance: int = 3,
        pinned: bool | None = None,
        source: str = "auto",
        now_dt: datetime | None = None,
    ) -> dict | None:
        clean_content = re.sub(r"\s+", " ", str(content or "")).strip(" \t\r\n")
        if len(clean_content) < 2:
            return None
        if len(clean_content) > 400:
            clean_content = clean_content[:400].rstrip()

        cat = str(category or "").strip().lower()
        if cat not in VALID_MEMORY_CATEGORIES:
            cat = infer_memory_category(clean_content)

        imp = max(1, min(5, int(importance or 3)))
        src = str(source or "auto").strip() or "auto"
        norm = strip_accents(clean_content)
        new_tokens = set(extract_informative_tokens(clean_content))
        now_str = self._now_iso(now_dt)

        with self._lock:
            self._ensure_schema()
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM memories ORDER BY pinned DESC, updated_at DESC"
                ).fetchall()

                best_match_row: sqlite3.Row | None = None
                best_sim = 0.0

                for r in rows:
                    old_content = str(r["content"])
                    old_norm = str(r["norm_text"])
                    if old_norm == norm:
                        best_match_row = r
                        best_sim = 1.0
                        break

                    old_tokens = set(extract_informative_tokens(old_content))
                    if not new_tokens or not old_tokens:
                        continue

                    if _has_conflicting_subject_or_entity(old_content, clean_content, old_tokens, new_tokens):
                        continue

                    dice = _dice_similarity(new_tokens, old_tokens)
                    jaccard = _jaccard_similarity(new_tokens, old_tokens)
                    same_cat = str(r["category"]) == cat

                    # Nếu một tập token chứa trọn tập kia (cùng chủ đề dự án/sở thích và >= 2 token đặc trưng)
                    is_subset_expansion = (
                        same_cat
                        and min(len(new_tokens), len(old_tokens)) >= 2
                        and (new_tokens.issubset(old_tokens) or old_tokens.issubset(new_tokens))
                    )

                    threshold_dice = 0.64 if same_cat else 0.76
                    threshold_jaccard = 0.50 if same_cat else 0.62

                    if is_subset_expansion or dice >= threshold_dice or jaccard >= threshold_jaccard:
                        sim_score = max(dice, 0.78 if is_subset_expansion else 0.0)
                        if sim_score > best_sim:
                            best_sim = sim_score
                            best_match_row = r

                if best_match_row is not None:
                    mem_id = int(best_match_row["id"])
                    old_content = str(best_match_row["content"])
                    old_norm = str(best_match_row["norm_text"])
                    old_tokens = set(extract_informative_tokens(old_content))
                    merged_imp = max(int(best_match_row["importance"]), imp)
                    merged_pin = (
                        int(bool(pinned))
                        if pinned is not None
                        else int(best_match_row["pinned"])
                    )
                    has_change_signal = _has_replacement_cue(clean_content) or _has_negation_flip(
                        old_content, clean_content
                    )
                    # Bảo toàn chi tiết phong phú hơn (hoặc bản có dấu tiếng Việt) khi câu tự học mới chỉ là tập con ngắn hơn
                    preserve_richer_old = (
                        src != "manual"
                        and not has_change_signal
                        and bool(old_tokens)
                        and new_tokens.issubset(old_tokens)
                        and (
                            len(old_tokens) > len(new_tokens)
                            or (old_norm == norm and old_content.lower() != old_norm and clean_content.lower() == norm)
                        )
                    )
                    updated_content = old_content if preserve_richer_old else clean_content
                    updated_norm = old_norm if preserve_richer_old else norm
                    conn.execute(
                        """
                        UPDATE memories
                        SET category = ?, content = ?, norm_text = ?, importance = ?, pinned = ?, source = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (cat, updated_content, updated_norm, merged_imp, merged_pin, src, now_str, mem_id),
                    )
                    conn.commit()
                    updated_row = conn.execute("SELECT * FROM memories WHERE id = ?", (mem_id,)).fetchone()
                    item = self._row_to_memory(updated_row)
                    item["_action"] = "updated"
                    return item

                pin_int = 1 if pinned else 0
                cur = conn.execute(
                    """
                    INSERT INTO memories (category, content, norm_text, importance, pinned, source, created_at, updated_at, last_used_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, '')
                    """,
                    (cat, clean_content, norm, imp, pin_int, src, now_str, now_str),
                )
                new_id = int(cur.lastrowid)

                # Giới hạn an toàn tối đa MAX_ACTIVE_MEMORIES: tự dọn ký ức cũ độ quan trọng thấp chưa ghim
                total_count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
                if total_count > MAX_ACTIVE_MEMORIES:
                    excess = total_count - MAX_ACTIVE_MEMORIES
                    conn.execute(
                        """
                        DELETE FROM memories
                        WHERE id IN (
                            SELECT id FROM memories
                            WHERE pinned = 0
                            ORDER BY importance ASC,
                                     CASE WHEN last_used_at = '' THEN updated_at ELSE last_used_at END ASC,
                                     id ASC
                            LIMIT ?
                        )
                        """,
                        (excess,),
                    )

                conn.commit()
                new_row = conn.execute("SELECT * FROM memories WHERE id = ?", (new_id,)).fetchone()
                if new_row is None:
                    return None
                item = self._row_to_memory(new_row)
                item["_action"] = "inserted"
                return item
            finally:
                conn.close()

    def list_memories(self, query: str = "", category: str = "", limit: int = 200) -> list[dict]:
        q_norm = strip_accents(query)
        cat = str(category or "").strip().lower()
        lim = max(1, min(500, int(limit or 200)))

        with self._lock:
            self._ensure_schema()
            conn = self._connect()
            try:
                sql = "SELECT * FROM memories WHERE 1=1"
                params: list = []
                if cat and cat in VALID_MEMORY_CATEGORIES:
                    sql += " AND category = ?"
                    params.append(cat)
                sql += " ORDER BY pinned DESC, importance DESC, updated_at DESC, id DESC"
                rows = conn.execute(sql, params).fetchall()
                items = [self._row_to_memory(r) for r in rows]
                if q_norm:
                    q_tokens = extract_informative_tokens(query) or q_norm.split()
                    filtered: list[dict] = []
                    for it in items:
                        if q_norm in it["norm_text"] or (
                            q_tokens and all(tok in it["norm_text"] for tok in q_tokens)
                        ):
                            filtered.append(it)
                    items = filtered
                return items[:lim]
            finally:
                conn.close()

    def update_memory(
        self,
        memory_id: int,
        content: str | None = None,
        category: str | None = None,
        importance: int | None = None,
        pinned: bool | None = None,
        now_dt: datetime | None = None,
    ) -> dict | None:
        with self._lock:
            self._ensure_schema()
            conn = self._connect()
            try:
                row = conn.execute("SELECT * FROM memories WHERE id = ?", (int(memory_id),)).fetchone()
                if row is None:
                    return None
                new_content = (
                    re.sub(r"\s+", " ", str(content)).strip()
                    if content is not None and str(content).strip()
                    else str(row["content"])
                )
                new_norm = strip_accents(new_content)
                new_cat = (
                    str(category).strip().lower()
                    if category is not None and str(category).strip().lower() in VALID_MEMORY_CATEGORIES
                    else str(row["category"])
                )
                new_imp = (
                    max(1, min(5, int(importance)))
                    if importance is not None
                    else int(row["importance"])
                )
                new_pin = int(bool(pinned)) if pinned is not None else int(row["pinned"])
                now_str = self._now_iso(now_dt)

                conn.execute(
                    """
                    UPDATE memories
                    SET content = ?, norm_text = ?, category = ?, importance = ?, pinned = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (new_content, new_norm, new_cat, new_imp, new_pin, now_str, int(memory_id)),
                )
                conn.commit()
                updated = conn.execute("SELECT * FROM memories WHERE id = ?", (int(memory_id),)).fetchone()
                return self._row_to_memory(updated) if updated else None
            finally:
                conn.close()

    def delete_memory(self, memory_id: int) -> bool:
        with self._lock:
            self._ensure_schema()
            conn = self._connect()
            try:
                cur = conn.execute("DELETE FROM memories WHERE id = ?", (int(memory_id),))
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def clear_all_memories(self) -> int:
        with self._lock:
            self._ensure_schema()
            conn = self._connect()
            try:
                cur = conn.execute("DELETE FROM memories")
                conn.commit()
                return int(cur.rowcount)
            finally:
                conn.close()

    def forget_memories_by_query(self, query: str) -> list[dict]:
        """Xóa các ký ức khớp ngữ nghĩa/từ khóa khi người dùng yêu cầu 'quên chuyện ... đi'."""
        q_norm = strip_accents(query)
        if not q_norm:
            return []
        clean_q_norm = re.sub(
            r"^(?:wisp\s+)?(?:oi\s+)?(?:hay\s+|lam\s+on\s+)?(?:quen|xoa|bo|dung\s+nho)"
            r"(?:\s+(?:chuyen|viec|thong\s+tin|ky\s+uc|ve|rang|la))*\s*",
            "",
            q_norm,
        ).strip()
        clean_q_norm = re.sub(
            r"\s+(?:di|nhe|nha|giup\s+minh|cho\s+minh|a)$",
            "",
            clean_q_norm,
        ).strip(" \"'“”.,!?")

        q_tokens = {
            t for t in extract_informative_tokens(query)
            if t not in _FORGET_QUERY_STOPWORDS
        }
        if not q_tokens:
            q_tokens = {
                t for t in re.findall(r"[a-z0-9]+", clean_q_norm or q_norm)
                if len(t) >= 2 and t not in _FORGET_QUERY_STOPWORDS and t not in _NORM_STOPWORDS
            }
        if not q_tokens:
            return []

        with self._lock:
            self._ensure_schema()
            conn = self._connect()
            try:
                rows = conn.execute("SELECT * FROM memories").fetchall()
                entries = [
                    (r, str(r["norm_text"]), set(extract_informative_tokens(str(r["content"]))))
                    for r in rows
                ]
                df = {
                    tok: sum(1 for _, _, m_toks in entries if tok in m_toks)
                    for tok in q_tokens
                }
                present_q_tokens = {tok for tok, cnt in df.items() if cnt > 0}
                anchor_tokens: set[str] = set()
                if present_q_tokens:
                    min_df = min(df[t] for t in present_q_tokens)
                    max_df = max(df[t] for t in present_q_tokens)
                    if min_df < max_df:
                        anchor_tokens = {t for t in present_q_tokens if df[t] == min_df}

                to_delete: list[dict] = []
                min_required = max(1, math.ceil(len(q_tokens) * 0.6))
                for r, m_norm, m_tokens in entries:
                    overlap = len(q_tokens & m_tokens)
                    exact_sub = bool(clean_q_norm) and len(clean_q_norm) >= 4 and clean_q_norm in m_norm
                    meets_anchor = (not anchor_tokens) or bool(m_tokens & anchor_tokens)
                    if exact_sub or (meets_anchor and overlap >= min_required):
                        to_delete.append(self._row_to_memory(r))

                if to_delete:
                    ids = [m["id"] for m in to_delete]
                    placeholders = ",".join("?" for _ in ids)
                    conn.execute(f"DELETE FROM memories WHERE id IN ({placeholders})", ids)
                    conn.commit()
                return to_delete
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # TASKS / REMINDERS CRUD
    # ------------------------------------------------------------------
    def add_task(
        self,
        title: str,
        due_text: str = "",
        due_iso: str = "",
        priority: int = 2,
        now_dt: datetime | None = None,
    ) -> dict | None:
        clean_title = re.sub(r"\s+", " ", str(title or "")).strip(" \t\r\n")
        if len(clean_title) < 2:
            return None
        if len(clean_title) > 300:
            clean_title = clean_title[:300].rstrip()

        d_text = str(due_text or "").strip()
        d_iso = str(due_iso or "").strip()
        if not d_text and not d_iso:
            d_text, d_iso = parse_vietnamese_due_time(clean_title, now_dt=now_dt)

        norm_t = strip_accents(clean_title)
        prio = max(1, min(3, int(priority or 2)))
        now_str = self._now_iso(now_dt)

        with self._lock:
            self._ensure_schema()
            conn = self._connect()
            try:
                # Chống tạo trùng công việc đang pending có cùng norm_title
                existing = conn.execute(
                    "SELECT * FROM tasks WHERE status = 'pending' AND norm_title = ? LIMIT 1",
                    (norm_t,),
                ).fetchone()
                if existing is not None:
                    task_id = int(existing["id"])
                    new_due_text = d_text or str(existing["due_text"])
                    new_due_iso = d_iso or str(existing["due_iso"])
                    conn.execute(
                        "UPDATE tasks SET title = ?, due_text = ?, due_iso = ?, priority = ? WHERE id = ?",
                        (clean_title, new_due_text, new_due_iso, max(prio, int(existing["priority"])), task_id),
                    )
                    conn.commit()
                    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
                    return self._row_to_task(row)

                cur = conn.execute(
                    """
                    INSERT INTO tasks (title, norm_title, due_text, due_iso, status, priority, created_at, completed_at)
                    VALUES (?, ?, ?, ?, 'pending', ?, ?, '')
                    """,
                    (clean_title, norm_t, d_text, d_iso, prio, now_str),
                )
                conn.commit()
                new_row = conn.execute("SELECT * FROM tasks WHERE id = ?", (int(cur.lastrowid),)).fetchone()
                return self._row_to_task(new_row) if new_row else None
            finally:
                conn.close()

    def list_tasks(self, status: str = "", limit: int = 200) -> list[dict]:
        st = str(status or "").strip().lower()
        lim = max(1, min(500, int(limit or 200)))
        with self._lock:
            self._ensure_schema()
            conn = self._connect()
            try:
                sql = "SELECT * FROM tasks"
                params: list = []
                if st in ("pending", "done"):
                    sql += " WHERE status = ?"
                    params.append(st)
                sql += (
                    " ORDER BY CASE WHEN status = 'pending' THEN 0 ELSE 1 END ASC,"
                    " CASE WHEN due_iso = '' THEN 1 ELSE 0 END ASC,"
                    " due_iso ASC, priority DESC, id DESC LIMIT ?"
                )
                params.append(lim)
                rows = conn.execute(sql, params).fetchall()
                return [self._row_to_task(r) for r in rows]
            finally:
                conn.close()

    def update_task_status(
        self,
        task_id: int,
        status: str,
        now_dt: datetime | None = None,
    ) -> dict | None:
        return self.update_task(task_id, status=status, now_dt=now_dt)

    def update_task(
        self,
        task_id: int,
        title: str | None = None,
        status: str | None = None,
        due_text: str | None = None,
        due_iso: str | None = None,
        priority: int | None = None,
        now_dt: datetime | None = None,
    ) -> dict | None:
        with self._lock:
            self._ensure_schema()
            conn = self._connect()
            try:
                row = conn.execute("SELECT * FROM tasks WHERE id = ?", (int(task_id),)).fetchone()
                if row is None:
                    return None
                new_title = (
                    re.sub(r"\s+", " ", str(title)).strip()
                    if title is not None and str(title).strip()
                    else str(row["title"])
                )
                new_norm = strip_accents(new_title)
                new_status = str(row["status"])
                completed_at = str(row["completed_at"] or "")
                if status is not None and str(status).strip().lower() in ("pending", "done"):
                    new_status = str(status).strip().lower()
                    completed_at = self._now_iso(now_dt) if new_status == "done" else ""
                new_due_text = str(due_text).strip() if due_text is not None else str(row["due_text"] or "")
                new_due_iso = str(due_iso).strip() if due_iso is not None else str(row["due_iso"] or "")
                new_prio = max(1, min(3, int(priority))) if priority is not None else int(row["priority"])

                conn.execute(
                    """
                    UPDATE tasks
                    SET title = ?, norm_title = ?, due_text = ?, due_iso = ?, status = ?, priority = ?, completed_at = ?
                    WHERE id = ?
                    """,
                    (new_title, new_norm, new_due_text, new_due_iso, new_status, new_prio, completed_at, int(task_id)),
                )
                conn.commit()
                updated = conn.execute("SELECT * FROM tasks WHERE id = ?", (int(task_id),)).fetchone()
                return self._row_to_task(updated) if updated else None
            finally:
                conn.close()

    def complete_task_by_query(self, query: str, now_dt: datetime | None = None) -> list[dict]:
        """Đánh dấu hoàn thành công việc khớp từ khóa khi người dùng báo 'Xong việc ... rồi'."""
        q_norm = strip_accents(query)
        if not q_norm:
            return []
        q_tokens = set(extract_informative_tokens(query))
        if not q_tokens:
            q_tokens = {t for t in q_norm.split() if len(t) >= 2}
        if not q_tokens:
            return []

        now_str = self._now_iso(now_dt)
        with self._lock:
            self._ensure_schema()
            conn = self._connect()
            try:
                rows = conn.execute("SELECT * FROM tasks WHERE status = 'pending' ORDER BY id DESC").fetchall()
                best_row: sqlite3.Row | None = None
                best_score = 0.0
                for r in rows:
                    t_norm = str(r["norm_title"])
                    t_tokens = set(extract_informative_tokens(str(r["title"])))
                    if q_norm in t_norm or t_norm in q_norm:
                        best_row = r
                        best_score = 1.0
                        break
                    overlap = len(q_tokens & t_tokens)
                    if overlap > 0:
                        score = overlap / max(1, min(len(q_tokens), len(t_tokens)))
                        if score >= 0.5 and score > best_score:
                            best_score = score
                            best_row = r

                if best_row is None:
                    return []
                tid = int(best_row["id"])
                conn.execute(
                    "UPDATE tasks SET status = 'done', completed_at = ? WHERE id = ?",
                    (now_str, tid),
                )
                conn.commit()
                updated = conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
                return [self._row_to_task(updated)] if updated else []
            finally:
                conn.close()

    def delete_task(self, task_id: int) -> bool:
        with self._lock:
            self._ensure_schema()
            conn = self._connect()
            try:
                cur = conn.execute("DELETE FROM tasks WHERE id = ?", (int(task_id),))
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def clear_all_tasks(self, only_done: bool = False) -> int:
        with self._lock:
            self._ensure_schema()
            conn = self._connect()
            try:
                if only_done:
                    cur = conn.execute("DELETE FROM tasks WHERE status = 'done'")
                else:
                    cur = conn.execute("DELETE FROM tasks")
                conn.commit()
                return int(cur.rowcount)
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # EPISODIC SUMMARIES (TÓM TẮT CUỘN PHIÊN HỘI THOẠI DÀI)
    # ------------------------------------------------------------------
    def add_episodic_summary(self, summary: str, now_dt: datetime | None = None) -> dict | None:
        clean_sum = re.sub(r"\s+", " ", str(summary or "")).strip()
        if not clean_sum:
            return None
        now_str = self._now_iso(now_dt)
        with self._lock:
            self._ensure_schema()
            conn = self._connect()
            try:
                cur = conn.execute(
                    "INSERT INTO episodic_summaries (summary, created_at) VALUES (?, ?)",
                    (clean_sum, now_str),
                )
                # Giữ tối đa 5 bản tóm tắt gần nhất của phiên hiện tại để prompt luôn tinh gọn
                conn.execute(
                    """
                    DELETE FROM episodic_summaries
                    WHERE id NOT IN (
                        SELECT id FROM episodic_summaries ORDER BY id DESC LIMIT 5
                    )
                    """
                )
                conn.commit()
                row = conn.execute("SELECT * FROM episodic_summaries WHERE id = ?", (int(cur.lastrowid),)).fetchone()
                return {"id": int(row["id"]), "summary": str(row["summary"]), "created_at": str(row["created_at"])}
            finally:
                conn.close()

    def list_episodic_summaries(self, limit: int = 3) -> list[dict]:
        lim = max(1, min(20, int(limit or 3)))
        with self._lock:
            self._ensure_schema()
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM episodic_summaries ORDER BY id DESC LIMIT ?",
                    (lim,),
                ).fetchall()
                return [
                    {"id": int(r["id"]), "summary": str(r["summary"]), "created_at": str(r["created_at"])}
                    for r in reversed(rows)
                ]
            finally:
                conn.close()

    def clear_episodic_summaries(self) -> int:
        with self._lock:
            self._ensure_schema()
            conn = self._connect()
            try:
                cur = conn.execute("DELETE FROM episodic_summaries")
                conn.commit()
                return int(cur.rowcount)
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # COUNTS & HYBRID BM25 CONTEXT RETRIEVAL
    # ------------------------------------------------------------------
    def get_counts(self) -> dict[str, int]:
        with self._lock:
            self._ensure_schema()
            conn = self._connect()
            try:
                mem_total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
                pin_total = conn.execute("SELECT COUNT(*) FROM memories WHERE pinned = 1").fetchone()[0]
                pending_tasks = conn.execute("SELECT COUNT(*) FROM tasks WHERE status = 'pending'").fetchone()[0]
                done_tasks = conn.execute("SELECT COUNT(*) FROM tasks WHERE status = 'done'").fetchone()[0]
                return {
                    "memories_total": int(mem_total),
                    "pinned_total": int(pin_total),
                    "pending_tasks": int(pending_tasks),
                    "done_tasks": int(done_tasks),
                    "tasks_total": int(pending_tasks) + int(done_tasks),
                }
            finally:
                conn.close()

    def retrieve_context_bundle(
        self,
        query: str,
        now_dt: datetime | None = None,
        max_memories: int = 8,
        max_tasks: int = 6,
        mark_used: bool = True,
    ) -> dict:
        """
        Xếp hạng & truy xuất ngữ cảnh trí nhớ theo công thức Hybrid:
        Score = 3.0 * Pinned + 2.2 * BM25_Overlap + 0.4 * Importance_Norm + 0.5 * Recency
        Kết hợp danh sách việc pending (ưu tiên sắp đến hạn/khớp câu hỏi) và bản tóm tắt phiên dài.
        """
        now = now_dt or datetime.now(VN_TZ)
        if now.tzinfo is None:
            now = now.replace(tzinfo=VN_TZ)

        q_norm = strip_accents(query)
        q_tokens = extract_informative_tokens(query)
        q_token_set = set(q_tokens)

        # Nhận diện ý định chủ đề để tăng điểm đúng nhóm phân loại (VD: hỏi "dự án" khớp category="project")
        boosted_categories: dict[str, float] = {}
        if re.search(r"\b(du an|project|app|ung dung|san pham|code|xay dung)\b", q_norm):
            boosted_categories["project"] = 0.75
        if re.search(r"\b(so thich|thich|ghet|an|uong|ca phe|mon|mac|trang phuc|choi|chay bo)\b", q_norm):
            boosted_categories["preference"] = 0.65
            boosted_categories["habit"] = 0.65
            boosted_categories["health"] = 0.55
        if re.search(r"\b(suc khoe|di ung|benh|dau|kieng|thuoc|ngu)\b", q_norm):
            boosted_categories["health"] = 0.75
        if re.search(r"\b(ky nang|ngon ngu|framework|swift|swiftui|flutter|python|lap trinh)\b", q_norm):
            boosted_categories["skill"] = 0.60
            boosted_categories.setdefault("project", 0.45)
        if re.search(r"\b(vo|chong|con|ban|sep|gia dinh|nguoi yeu)\b", q_norm):
            boosted_categories["relationship"] = 0.75

        with self._lock:
            self._ensure_schema()
            conn = self._connect()
            try:
                mem_rows = conn.execute("SELECT * FROM memories").fetchall()
                scored_memories: list[dict] = []

                if mem_rows:
                    doc_tokens_list: list[list[str]] = []
                    df: dict[str, int] = {}
                    for r in mem_rows:
                        dtoks = extract_informative_tokens(str(r["content"]))
                        doc_tokens_list.append(dtoks)
                        for tok in set(dtoks):
                            df[tok] = df.get(tok, 0) + 1

                    n_docs = len(mem_rows)
                    avgdl = max(1.0, sum(len(t) for t in doc_tokens_list) / n_docs)
                    k1 = 1.5
                    b = 0.75

                    raw_bm25_scores: list[float] = []
                    for idx, r in enumerate(mem_rows):
                        dtoks = doc_tokens_list[idx]
                        dl = max(1, len(dtoks))
                        tf_map: dict[str, int] = {}
                        for t in dtoks:
                            tf_map[t] = tf_map.get(t, 0) + 1

                        bm25_val = 0.0
                        for qt in q_token_set:
                            tf = tf_map.get(qt, 0)
                            if tf <= 0:
                                # Khớp chuỗi con cho tên riêng ghép (vd: "testflight", "fitnote")
                                if len(qt) >= 4 and qt in str(r["norm_text"]):
                                    tf = 1
                                else:
                                    continue
                            doc_freq = df.get(qt, 0)
                            idf = math.log(1.0 + (n_docs - doc_freq + 0.5) / (doc_freq + 0.5))
                            denom = tf + k1 * (1.0 - b + b * (dl / avgdl))
                            bm25_val += idf * ((tf * (k1 + 1.0)) / max(0.001, denom))
                        raw_bm25_scores.append(bm25_val)

                    max_bm25 = max(raw_bm25_scores) if raw_bm25_scores else 0.0

                    for idx, r in enumerate(mem_rows):
                        item = self._row_to_memory(r)
                        pinned_val = 1.0 if item["pinned"] else 0.0
                        bm25_norm = (raw_bm25_scores[idx] / max_bm25) if max_bm25 > 0 else 0.0
                        cat_boost = boosted_categories.get(item["category"], 0.0)
                        bm25_overlap = min(1.35, bm25_norm + cat_boost)

                        imp_norm = max(0.2, min(1.0, item["importance"] / 5.0))
                        try:
                            upd_dt = datetime.fromisoformat(item["updated_at"])
                            if upd_dt.tzinfo is None:
                                upd_dt = upd_dt.replace(tzinfo=VN_TZ)
                            age_days = max(0.0, (now - upd_dt).total_seconds() / 86400.0)
                        except Exception:
                            age_days = 0.0
                        recency_val = 1.0 / (1.0 + age_days / 14.0)

                        total_score = (
                            3.0 * pinned_val
                            + 2.2 * bm25_overlap
                            + 0.4 * imp_norm
                            + 0.5 * recency_val
                        )
                        item["score"] = round(total_score, 4)
                        scored_memories.append(item)

                    scored_memories.sort(key=lambda m: (m["score"], m["id"]), reverse=True)
                    scored_memories = scored_memories[: max(1, int(max_memories))]

                    if mark_used and scored_memories:
                        now_str = self._now_iso(now)
                        used_ids = [m["id"] for m in scored_memories]
                        ph = ",".join("?" for _ in used_ids)
                        conn.execute(
                            f"UPDATE memories SET last_used_at = ? WHERE id IN ({ph})",
                            [now_str, *used_ids],
                        )
                        conn.commit()

                # Truy xuất danh sách việc pending, ưu tiên việc khớp từ khóa/ngày hỏi & sắp đến hạn
                task_rows = conn.execute(
                    """
                    SELECT * FROM tasks
                    WHERE status = 'pending'
                    ORDER BY CASE WHEN due_iso = '' THEN 1 ELSE 0 END ASC,
                             due_iso ASC, priority DESC, id DESC
                    """
                ).fetchall()
                all_pending = [self._row_to_task(r) for r in task_rows]

                def _task_rank(t: dict) -> tuple[int, int, str, int]:
                    t_haystack = strip_accents(f"{t['title']} {t['due_text']}")
                    tok_hit = sum(1 for qt in q_token_set if qt in t_haystack)
                    # Kiểm tra khớp thứ trong tuần nếu câu hỏi nhắc đến thứ
                    for wd in WEEKDAY_VI:
                        wd_n = strip_accents(wd)
                        if wd_n in q_norm and wd_n in t_haystack:
                            tok_hit += 2
                    has_due = 0 if t["due_iso"] else 1
                    return (-tok_hit, has_due, t["due_iso"] or "9999-99-99", -t["priority"])

                all_pending.sort(key=_task_rank)
                selected_tasks = all_pending[: max(1, int(max_tasks))]

                sum_rows = conn.execute(
                    "SELECT * FROM episodic_summaries ORDER BY id DESC LIMIT 2"
                ).fetchall()
                summaries = [
                    {"id": int(r["id"]), "summary": str(r["summary"]), "created_at": str(r["created_at"])}
                    for r in reversed(sum_rows)
                ]

                return {
                    "memories": scored_memories,
                    "tasks": selected_tasks,
                    "summaries": summaries,
                }
            finally:
                conn.close()


# Singleton mặc định cho toàn ứng dụng (hỗ trợ set_db_path để kiểm thử cô lập)
_STORE_LOCK = threading.RLock()
_DEFAULT_STORE = MemoryStore()


def get_store() -> MemoryStore:
    with _STORE_LOCK:
        return _DEFAULT_STORE


def get_db_path() -> str:
    with _STORE_LOCK:
        return _DEFAULT_STORE.db_path


def set_db_path(db_path: str | None) -> MemoryStore:
    """Chuyển đường dẫn DB của singleton (phục vụ kiểm thử cô lập trên file tạm)."""
    global _DEFAULT_STORE
    with _STORE_LOCK:
        _DEFAULT_STORE = MemoryStore(db_path=db_path or _resolve_initial_db_path())
        return _DEFAULT_STORE


# Các hàm module-level tiện lợi
def upsert_memory(
    content: str,
    category: str = "auto",
    importance: int = 3,
    pinned: bool | None = None,
    source: str = "auto",
    now_dt: datetime | None = None,
) -> dict | None:
    return get_store().upsert_memory(
        content=content,
        category=category,
        importance=importance,
        pinned=pinned,
        source=source,
        now_dt=now_dt,
    )


def list_memories(query: str = "", category: str = "", limit: int = 200) -> list[dict]:
    return get_store().list_memories(query=query, category=category, limit=limit)


def update_memory(
    memory_id: int,
    content: str | None = None,
    category: str | None = None,
    importance: int | None = None,
    pinned: bool | None = None,
    now_dt: datetime | None = None,
) -> dict | None:
    return get_store().update_memory(
        memory_id=memory_id,
        content=content,
        category=category,
        importance=importance,
        pinned=pinned,
        now_dt=now_dt,
    )


def delete_memory(memory_id: int) -> bool:
    return get_store().delete_memory(memory_id)


def clear_all_memories() -> int:
    return get_store().clear_all_memories()


def forget_memories_by_query(query: str) -> list[dict]:
    return get_store().forget_memories_by_query(query)


def add_task(
    title: str,
    due_text: str = "",
    due_iso: str = "",
    priority: int = 2,
    now_dt: datetime | None = None,
) -> dict | None:
    return get_store().add_task(
        title=title,
        due_text=due_text,
        due_iso=due_iso,
        priority=priority,
        now_dt=now_dt,
    )


def list_tasks(status: str = "", limit: int = 200) -> list[dict]:
    return get_store().list_tasks(status=status, limit=limit)


def update_task_status(task_id: int, status: str, now_dt: datetime | None = None) -> dict | None:
    return get_store().update_task_status(task_id=task_id, status=status, now_dt=now_dt)


def update_task(
    task_id: int,
    title: str | None = None,
    status: str | None = None,
    due_text: str | None = None,
    due_iso: str | None = None,
    priority: int | None = None,
    now_dt: datetime | None = None,
) -> dict | None:
    return get_store().update_task(
        task_id=task_id,
        title=title,
        status=status,
        due_text=due_text,
        due_iso=due_iso,
        priority=priority,
        now_dt=now_dt,
    )


def complete_task_by_query(query: str, now_dt: datetime | None = None) -> list[dict]:
    return get_store().complete_task_by_query(query=query, now_dt=now_dt)


def delete_task(task_id: int) -> bool:
    return get_store().delete_task(task_id)


def clear_all_tasks(only_done: bool = False) -> int:
    return get_store().clear_all_tasks(only_done=only_done)


def add_episodic_summary(summary: str, now_dt: datetime | None = None) -> dict | None:
    return get_store().add_episodic_summary(summary=summary, now_dt=now_dt)


def list_episodic_summaries(limit: int = 3) -> list[dict]:
    return get_store().list_episodic_summaries(limit=limit)


def clear_episodic_summaries() -> int:
    return get_store().clear_episodic_summaries()


def get_counts() -> dict[str, int]:
    return get_store().get_counts()


def retrieve_context_bundle(
    query: str,
    now_dt: datetime | None = None,
    max_memories: int = 8,
    max_tasks: int = 6,
    mark_used: bool = True,
) -> dict:
    return get_store().retrieve_context_bundle(
        query=query,
        now_dt=now_dt,
        max_memories=max_memories,
        max_tasks=max_tasks,
        mark_used=mark_used,
    )
