"""
Flask backend cho Bot Local Web (Windows).
Cung cấp giao diện web tại / và các API /api/chat, /api/chat/stream, /api/voices, /api/tts, /api/health.
"""
import asyncio
import base64
from collections import OrderedDict
import http.client
import json
import queue
import re
import sys
import urllib.parse
import urllib.request
from flask import Flask, Response, jsonify, render_template, request, stream_with_context

import bot
from bot import (
    GREETING,
    clear_chat_history,
    get_chat_history,
    get_reply,
    get_user_profile,
    normalize_vietnamese_input,
    reset_user_profile,
    restore_chat_history,
    stream_reply,
    update_user_profile,
)

# Đảm bảo console Windows hiển thị đúng tiếng Việt và emoji UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

app = Flask(__name__)
app.json.ensure_ascii = False
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

import threading

# Danh sách các giọng đọc phong cách Google NotebookLM / ChatGPT Voice hỗ trợ chọn trên giao diện
VOICE_PROFILES = OrderedDict([
    (
        "hoaimy",
        {
            "id": "hoaimy",
            "name": "Linh Đan · Nữ Podcast (NotebookLM)",
            "engine": "edge",
            "voice": "vi-VN-HoaiMyNeural",
            "rate": "+2%",
            "pitch": "-1Hz",
        },
    ),
    (
        "namminh",
        {
            "id": "namminh",
            "name": "Minh Khôi · Nam Podcast (NotebookLM)",
            "engine": "edge",
            "voice": "vi-VN-NamMinhNeural",
            "rate": "+3%",
            "pitch": "-2Hz",
        },
    ),
    (
        "hoaimy_fast",
        {
            "id": "hoaimy_fast",
            "name": "Breeze · Nữ Tự nhiên (ChatGPT Voice)",
            "engine": "edge",
            "voice": "vi-VN-HoaiMyNeural",
            "rate": "+8%",
            "pitch": "+1Hz",
        },
    ),
    (
        "namminh_deep",
        {
            "id": "namminh_deep",
            "name": "Cove · Nam Trầm ấm (ChatGPT Voice)",
            "engine": "edge",
            "voice": "vi-VN-NamMinhNeural",
            "rate": "+5%",
            "pitch": "-1Hz",
        },
    ),
    (
        "ava_multilingual",
        {
            "id": "ava_multilingual",
            "name": "Ava · Đa ngôn ngữ AI (Song ngữ Anh-Việt)",
            "engine": "edge",
            "voice": "en-US-AvaMultilingualNeural",
            "rate": "+0%",
            "pitch": "+0Hz",
        },
    ),
])

DEFAULT_VOICE_ID = "hoaimy"
VOICE_PREVIEW_SAMPLES: dict[str, str] = {
    "hoaimy": "Chào bạn! Mình là Wisp, rất vui được hỗ trợ bạn.",
    "namminh": "Chào bạn! Mình là Wisp, bạn cần mình hỗ trợ gì hôm nay cứ nói nhé.",
    "hoaimy_fast": "Chào bạn! Mình là Wisp, sẵn sàng hỗ trợ bạn ngay đây.",
    "namminh_deep": "Chào bạn, mình là Wisp. Cần hỏi thời tiết hay viết code cứ bảo mình nhé.",
    "ava_multilingual": "Chào bạn! Mình là Wisp, trợ lý AI song ngữ của bạn.",
}
_TTS_CACHE: OrderedDict[tuple[str, str], bytes] = OrderedDict()
_TTS_CACHE_MAX = 96

# Persistent background asyncio event loop để không tốn thời gian tạo loop mới mỗi lần đọc TTS
_BG_LOOP = asyncio.new_event_loop()
_BG_THREAD = threading.Thread(target=_BG_LOOP.run_forever, daemon=True)
_BG_THREAD.start()


def _run_async(coro, timeout: float = 12.0):
    future = asyncio.run_coroutine_threadsafe(coro, _BG_LOOP)
    return future.result(timeout=timeout)


def _format_num_vn(val_str: str) -> str:
    """Làm gọn số thập phân .0 (vd 28.0 -> 28, 28.5 -> 28 phẩy 5) để đọc tự nhiên như người thật."""
    s = val_str.strip()
    if s.endswith(".0") or s.endswith(",0"):
        return s[:-2]
    return re.sub(r"(\d+)[.,](\d+)", r"\1 phẩy \2", s)


def _prepare_tts_text(raw_text: str) -> str:
    """
    Chuyển đổi câu trả lời dạng thẻ/bullet sang VĂN NÓI HỘI THOẠI TỰ NHIÊN
    (phong cách Google NotebookLM / ChatGPT Voice Mode) để giọng đọc có hồn, liền mạch,
    tuyệt đối không đọc máy móc từng dấu hai chấm, dấu ngoặc hay gạch đầu dòng.
    """
    s = str(raw_text or "").strip()
    if not s:
        return ""

    # 1a. Chuyển hóa dòng Thời tiết ngắn gọn (chế độ Concise) sang câu nói súc tích
    m_cur_concise = re.search(
        r"^(?:🌤️\s*)?([^\n:]+):\s*([\d.,]+)\s*°C(?:\s*\(như\s*([\d.,]+)\s*°C\))?,\s*([^\n,]+(?:,\s*[^\n,]+)?),\s*ẩm\s*([\d.,]+)%,\s*gió\s*([\d.,]+)\s*km/h\.$",
        s,
    )
    if m_cur_concise:
        place, temp, _feels, desc, hum, _wind = m_cur_concise.groups()
        desc_clean = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\ufe0e\ufe0f]", "", desc).strip().lower()
        t_vn = _format_num_vn(temp)
        h_vn = _format_num_vn(hum)
        return f"{place.strip()} {t_vn} độ, {desc_clean}, độ ẩm {h_vn} phần trăm."

    # 1b. Chuyển hóa thẻ Thời tiết hiện tại đầy đủ sang câu nói tự nhiên
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
        desc_clean = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\ufe0e\ufe0f]", "", desc).strip().lower()
        t_vn = _format_num_vn(temp)
        f_vn = _format_num_vn(feels)
        h_vn = _format_num_vn(hum)
        w_vn = _format_num_vn(wind)
        if t_vn == f_vn:
            return (
                f"Hiện tại ở {place.strip()}, {desc_clean}, nhiệt độ khoảng {t_vn} độ C. "
                f"Độ ẩm ở mức {h_vn} phần trăm và gió nhẹ khoảng {w_vn} ki lô mét trên giờ nhé."
            )
        return (
            f"Hiện tại ở {place.strip()}, {desc_clean}, nhiệt độ khoảng {t_vn} độ, "
            f"nhưng cảm giác thực tế ngoài trời khoảng {f_vn} độ. "
            f"Độ ẩm {h_vn} phần trăm và sức gió {w_vn} ki lô mét trên giờ nhé."
        )

    # 2a. Chuyển hóa dòng Dự báo ngày mai ngắn gọn (chế độ Concise)
    m_tom_concise = re.search(
        r"^(?:🌤️\s*)?Ngày mai tại\s+([^\n:]+):\s*([\d.,]+)\s*°C\s*[–\-~]\s*([\d.,]+)\s*°C,\s*([^\n,]+(?:,\s*[^\n,]+)?)(?:,\s*mưa\s*([\d.,]+)%)?\.$",
        s,
    )
    if m_tom_concise:
        place, tmin, tmax, desc, rain = m_tom_concise.groups()
        desc_clean = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\ufe0e\ufe0f]", "", desc).strip().lower()
        tmin_vn = _format_num_vn(tmin)
        tmax_vn = _format_num_vn(tmax)
        rain_part = f", mưa {_format_num_vn(rain)} phần trăm" if rain else ""
        return f"Ngày mai ở {place.strip()} từ {tmin_vn} đến {tmax_vn} độ, {desc_clean}{rain_part}."

    # 2b. Chuyển hóa thẻ Dự báo ngày mai đầy đủ sang câu nói hội thoại tự nhiên
    m_tom = re.search(
        r"Dự báo ngày mai tại\s+([^\n:]+):\s*\n"
        r"•\s*([^\n]+)\n"
        r"•\s*Nhiệt độ:\s*([\d.,]+)\s*°C\s*[–\-~]\s*([\d.,]+)\s*°C"
        r"(?:\s*\n•\s*Khả năng mưa:\s*([\d.,]+)%)?",
        s,
    )
    if m_tom:
        place, desc, tmin, tmax, rain = m_tom.groups()
        desc_clean = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\ufe0e\ufe0f]", "", desc).strip().lower()
        tmin_vn = _format_num_vn(tmin)
        tmax_vn = _format_num_vn(tmax)
        rain_clause = f", khả năng có mưa khoảng {_format_num_vn(rain)} phần trăm" if rain else ""
        return (
            f"Dự báo ngày mai ở {place.strip()}, {desc_clean}, "
            f"nhiệt độ dao động từ {tmin_vn} đến {tmax_vn} độ C{rain_clause} nhé."
        )

    # 3. Chuyển hóa câu trả lời Giờ giấc hiện tại (bỏ giây và bỏ cụm "(giờ Việt Nam)" để nói tự nhiên)
    m_time = re.search(
        r"Bây giờ là\s+(\d{1,2}):(\d{2})(?::\d{2})?\s*—\s*([^,]+),\s*(\d{1,2})/(\d{1,2})/(\d{4})",
        s,
    )
    if m_time:
        hh, mm, weekday, dd, mo, yyyy = m_time.groups()
        return f"Bây giờ là {int(hh)} giờ {int(mm)} phút, {weekday.strip()} ngày {int(dd)} tháng {int(mo)} năm {yyyy} nhé."

    # 4. Chuyển hóa câu trả lời văn bản chung / AI LLM sang văn nói mượt mà
    s = re.sub(r"```[\s\S]*?```", " Đã có đoạn mã nguồn trên màn hình. ", s)
    s = re.sub(r"`([^`]+)`", r"\1", s)
    s = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\ufe0e\ufe0f]", " ", s)

    # Chuẩn hóa đường dẫn Markdown Link [Anchor](url) -> giữ Anchor
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    # Chuẩn hóa URL trần -> "đường dẫn liên kết"
    s = re.sub(r"https?://\S+", " đường dẫn liên kết ", s)

    # Chuẩn hóa các từ viết tắt danh xưng / hành chính
    s = re.sub(r"\bTP\.?\s*(?:HCM|Hồ\s+Chí\s+Minh)\b", "Thành phố Hồ Chí Minh", s, flags=re.IGNORECASE)
    s = re.sub(r"\bTP\.\s*", "Thành phố ", s)
    s = re.sub(r"\bTS\.\s*", "Tiến sĩ ", s)
    s = re.sub(r"\bThS\.\s*", "Thạc sĩ ", s)
    s = re.sub(r"\bBS\.\s*", "Bác sĩ ", s)
    s = re.sub(r"\bMr\.\s*", "Mister ", s)
    s = re.sub(r"\bDr\.\s*", "Doctor ", s)

    # Chuẩn hóa số phiên bản phần mềm (vd: "Python 3.11" -> "Python 3 chấm 11", "iOS 18.2" -> "iOS 18 chấm 2")
    # Thực hiện TRƯỚC khi chuẩn hóa dấu chấm số thập phân (\d+\.\d+)
    s = re.sub(
        r"\b((?:Python|Swift|iOS|macOS|Android|Flutter|v|version|bản|phiên\s+bản)\s+)(\d+)\.(\d+)(?:\.(\d+))?\b",
        lambda m: f"{m.group(1)}{m.group(2)} chấm {m.group(3)}" + (f" chấm {m.group(4)}" if m.group(4) else ""),
        s,
        flags=re.IGNORECASE,
    )

    # Chuẩn hóa tiền tệ hàng nghìn / triệu / tỷ (hỗ trợ cả có dấu chấm phân cách và viết liền không dấu)
    def _currency_sub(m):
        num_str = re.sub(r"[.,]", "", m.group(1))
        try:
            val = int(num_str)
        except ValueError:
            return m.group(0)
        if val >= 1_000_000_000:
            ty = val // 1_000_000_000
            rem = val % 1_000_000_000
            if rem == 0:
                return f"{ty} tỷ đồng"
            # Chuẩn hóa các số thập phân thông dụng: 1.5B -> '1 phẩy 5 tỷ đồng', 1.25B -> '1 phẩy 25 tỷ đồng'
            if rem % 100_000_000 == 0:
                d = rem // 100_000_000
                return f"{ty} phẩy {d} tỷ đồng"
            if rem % 10_000_000 == 0 and rem in (250_000_000, 750_000_000):
                d = rem // 10_000_000
                return f"{ty} phẩy {d} tỷ đồng"
            # Phân rã nguyên bản bảo toàn chính xác từng đồng cho các mệnh giá lẻ
            trieu = rem // 1_000_000
            rem_m = rem % 1_000_000
            nghin = rem_m // 1_000
            dong = rem_m % 1_000
            parts = [f"{ty} tỷ"]
            if trieu > 0:
                parts.append(f"{trieu} triệu")
            if nghin > 0:
                parts.append(f"{nghin} nghìn")
            if dong > 0:
                parts.append(f"{dong}")
            return " ".join(parts) + " đồng"
        if val >= 1_000_000:
            if val % 1_000_000 == 0:
                return f"{val // 1_000_000} triệu đồng"
            if val % 1_000 == 0:
                trieu = val // 1_000_000
                nghin = (val % 1_000_000) // 1_000
                return f"{trieu} triệu {nghin} nghìn đồng"
            trieu = val // 1_000_000
            rem = val % 1_000_000
            nghin = rem // 1_000
            dong = rem % 1_000
            parts = [f"{trieu} triệu"]
            if nghin > 0:
                parts.append(f"{nghin} nghìn")
            if dong > 0:
                parts.append(f"{dong}")
            return " ".join(parts) + " đồng"
        if val >= 1_000:
            if val % 1_000 == 0:
                return f"{val // 1_000} nghìn đồng"
            nghin = val // 1_000
            dong = val % 1_000
            return f"{nghin} nghìn {dong} đồng"
        return f"{val} đồng"

    s = re.sub(
        r"(?<![.,\d])\b(\d{1,3}(?:[.,]\d{3})+|\d+)\s*(đồng|đ|vnd|vnđ)(?!\w)",
        _currency_sub,
        s,
        flags=re.IGNORECASE,
    )

    # Biến các gạch đầu dòng thành nhịp nghỉ câu tự nhiên (yêu cầu khoảng trắng theo chuẩn Markdown, tránh nuốt số thập phân / nhiệt độ / số âm)
    s = re.sub(r"(?:^|\n)\s*(?:[•*]|\-(?!\d)|\d{1,9}[.)])(?:\s+|$)", ". ", s)
    s = re.sub(r"\s*—\s*", ", ", s)
    s = re.sub(r"\(giờ Việt Nam\)", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\(([^)]+)\)", r", \1, ", s)
    s = re.sub(r"[*_#~>|•]", " ", s)

    # Chuẩn hóa giờ chẵn (10:00 -> 10 giờ) và giờ có phút (10:15 -> 10 giờ 15 phút)
    s = re.sub(r"\b(\d{1,2}):00(?::00)?\b", r"\1 giờ", s)
    s = re.sub(r"\b(\d{1,2}):(\d{2}):(\d{2})\b", r"\1 giờ \2 phút", s)
    s = re.sub(r"\b(\d{1,2}):(\d{2})\b", r"\1 giờ \2 phút", s)

    # Chuẩn hóa ngày tháng DD/MM/YYYY
    s = re.sub(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", r"\1 tháng \2 năm \3", s)

    # Chuẩn hóa khoảng nhiệt độ và đơn vị
    s = re.sub(r"(\d+(?:[.,]\d+)?)\s*°\s*C\s*[–\-~]\s*(\d+(?:[.,]\d+)?)\s*°\s*C", r"\1 đến \2 độ C", s)
    s = re.sub(r"°\s*C\b", " độ C", s)
    s = re.sub(r"(\d+(?:[.,]\d+)?)\s*%", r"\1 phần trăm", s)
    s = re.sub(r"\bkm/h\b", "ki lô mét trên giờ", s, flags=re.IGNORECASE)
    s = re.sub(r"(\d+)\.0\b", r"\1", s)
    s = re.sub(r"(\d+)\.(\d+)", r"\1 phẩy \2", s)

    # Dọn dấu câu thừa để nhịp ngắt nghỉ của giọng đọc tự nhiên nhất
    s = re.sub(r"\s*([,.!?])\s*", r"\1 ", s)
    s = re.sub(r"([,.!?]){2,}", r"\1", s)
    s = re.sub(r"^\s*[,.]\s*", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _trim_mp3_leading_silence(mp3_bytes: bytes) -> bytes:
    """
    Cắt bỏ các frame MP3 im lặng ở đầu file (MPEG-2 Layer III 24kHz 48kbps mono CBR = 288 bytes/frame = 24ms/frame)
    để âm thanh phát ra ngay lập tức không có khoảng lặng đầu câu.
    """
    if len(mp3_bytes) < 288 * 4:
        return mp3_bytes
    if mp3_bytes[0] != 0xFF or (mp3_bytes[1] & 0xF0) != 0xF0:
        return mp3_bytes

    frame_size = 288
    num_frames = len(mp3_bytes) // frame_size
    first_voice_frame = 0

    for i in range(min(num_frames, 30)):
        frame = mp3_bytes[i * frame_size : (i + 1) * frame_size]
        if len(frame) < frame_size or frame[0] != 0xFF:
            return mp3_bytes
        payload = frame[13:]
        active_bytes = sum(1 for b in payload if b not in (0x00, 0xFF, 0x55, 0xAA))
        if active_bytes > 24:
            first_voice_frame = i
            break

    cut_frames = max(0, first_voice_frame - 1)
    if cut_frames > 0:
        return mp3_bytes[cut_frames * frame_size :]
    return mp3_bytes


_EDGE_CONNECTOR = None
_GOOGLE_TLS_LOCAL = threading.local()


async def _get_edge_connector():
    """Tái sử dụng kết nối TLS/DNS keep-alive trên _BG_LOOP cho edge_tts."""
    global _EDGE_CONNECTOR
    if _EDGE_CONNECTOR is None or _EDGE_CONNECTOR.closed:
        import aiohttp

        class _PersistentTCPConnector(aiohttp.TCPConnector):
            async def close(self, abort_ssl: bool = False) -> None:
                # Giữ kết nối keep-alive sống xuyên suốt vòng đời server khi ClientSession đóng
                pass

        _EDGE_CONNECTOR = _PersistentTCPConnector(keepalive_timeout=300, ttl_dns_cache=300)
    return _EDGE_CONNECTOR


async def _synthesize_edge_tts(text: str, voice: str, rate: str, pitch: str) -> bytes:
    import edge_tts

    connector = await _get_edge_connector() if asyncio.get_running_loop() is _BG_LOOP else None
    communicate = edge_tts.Communicate(
        text=text,
        voice=voice,
        rate=rate,
        pitch=pitch,
        connector=connector,
    )
    chunks: list[bytes] = []
    async for msg in communicate.stream():
        if msg.get("type") == "audio" and msg.get("data"):
            chunks.append(msg["data"])
    return _trim_mp3_leading_silence(b"".join(chunks))


def _fetch_google_tts_segment(seg: str) -> bytes:
    """Tải 1 đoạn MP3 từ translate.google.com qua kết nối HTTPS Keep-Alive tái sử dụng (~140-260ms)."""
    q = urllib.parse.quote(seg)
    path = f"/translate_tts?ie=UTF-8&client=tw-ob&tl=vi&q={q}"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Connection": "keep-alive",
    }
    for attempt in range(2):
        conn = getattr(_GOOGLE_TLS_LOCAL, "conn", None)
        try:
            if conn is None:
                conn = http.client.HTTPSConnection("translate.google.com", timeout=3.5)
                _GOOGLE_TLS_LOCAL.conn = conn
            conn.request("GET", path, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            if resp.status == 200 and data:
                return data
            conn.close()
            _GOOGLE_TLS_LOCAL.conn = None
        except Exception:
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass
            _GOOGLE_TLS_LOCAL.conn = None
            if attempt == 1:
                url = f"https://translate.google.com{path}"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=4) as uresp:
                    return uresp.read()
    return b""


def _synthesize_google_tts(text: str) -> bytes:
    """Dự phòng tốc độ cao qua HTTPS Keep-Alive (~140-280ms)."""
    words = text.split()
    segments: list[str] = []
    current = ""
    for w in words:
        if len(current) + len(w) + 1 > 175:
            if current:
                segments.append(current)
            current = w
        else:
            current = f"{current} {w}".strip()
    if current:
        segments.append(current)

    audio_parts: list[bytes] = []
    for seg in segments:
        part = _fetch_google_tts_segment(seg)
        if part:
            audio_parts.append(part)
    return _trim_mp3_leading_silence(b"".join(audio_parts))


def _store_tts_cache(cache_key: tuple[str, str], mp3_bytes: bytes) -> None:
    if not mp3_bytes:
        return
    _TTS_CACHE[cache_key] = mp3_bytes
    _TTS_CACHE.move_to_end(cache_key)
    while len(_TTS_CACHE) > _TTS_CACHE_MAX:
        _TTS_CACHE.popitem(last=False)


async def _async_generate_tts(
    raw_text: str,
    voice_id: str = DEFAULT_VOICE_ID,
    fast_first: bool = False,
) -> bytes:
    """
    Sinh MP3 bất đồng bộ với Edge Neural TTS là luồng chính (không đua song song):
    - Edge Neural TTS chạy chính thức với 1 fast-retry khi gặp lỗi mạng/timeout và ngưỡng an toàn 2.2s.
    - Fallback Google TTS chỉ được gọi khi Edge TTS thất bại hoặc quá 2.2s.
    - Tách biệt hoàn toàn cache key: Google TTS fallback lưu dưới key ("google_fallback", clean_text),
      tuyệt đối không ghi đè vào cache key của giọng Nam (namminh / namminh_deep) hay giọng đã chọn.
    """
    clean_text = _prepare_tts_text(raw_text)
    if not clean_text:
        return b""

    profile = VOICE_PROFILES.get(voice_id) or VOICE_PROFILES[DEFAULT_VOICE_ID]
    actual_id = profile["id"]
    cache_key = (actual_id, clean_text)

    if cache_key in _TTS_CACHE:
        _TTS_CACHE.move_to_end(cache_key)
        return _TTS_CACHE[cache_key]

    # 1. Luồng chính: Edge Neural TTS (Primary Engine) với 1 retry nhanh
    if profile["engine"] == "edge":
        for attempt in range(2):
            try:
                edge_bytes = await asyncio.wait_for(
                    _synthesize_edge_tts(
                        clean_text,
                        voice=profile["voice"],
                        rate=profile["rate"],
                        pitch=profile["pitch"],
                    ),
                    timeout=2.2,
                )
                if edge_bytes:
                    _store_tts_cache(cache_key, edge_bytes)
                    return edge_bytes
            except (asyncio.TimeoutError, Exception):
                if attempt == 0:
                    await asyncio.sleep(0.04)
                    continue
                break

    # 2. Luồng dự phòng an toàn: Google Translate TTS (HTTPS Keep-Alive)
    fallback_cache_key = ("google_fallback", clean_text)
    if fallback_cache_key in _TTS_CACHE:
        _TTS_CACHE.move_to_end(fallback_cache_key)
        return _TTS_CACHE[fallback_cache_key]

    loop = asyncio.get_running_loop()
    try:
        google_bytes = await asyncio.wait_for(
            loop.run_in_executor(None, _synthesize_google_tts, clean_text),
            timeout=2.5,
        )
        if google_bytes:
            _store_tts_cache(fallback_cache_key, google_bytes)
            return google_bytes
    except Exception:
        pass

    return b""


def generate_vietnamese_tts_mp3(raw_text: str, voice_id: str = DEFAULT_VOICE_ID) -> bytes:
    """Sinh dữ liệu MP3 tiếng Việt theo giọng đã chọn, có cache RAM và persistent event loop."""
    try:
        return _run_async(_async_generate_tts(raw_text, voice_id), timeout=5.0)
    except Exception:
        return b""


def _split_tts_clauses(raw_text: str) -> list[str]:
    """
    Tách văn bản trả lời thành các vế câu ngắn gọn để đọc cuốn chiếu tức thì:
    - Vế đầu tiên được ưu tiên ngắt ngắn ngay tại dấu phẩy đầu tiên (~10-45 ký tự) để phát ra loa tức khắc (~180ms).
    - Các câu tiếp theo được gom theo từng câu tự nhiên.
    """
    clean = _prepare_tts_text(raw_text)
    if not clean:
        return []

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", clean) if s.strip()]
    if not sentences:
        return [clean]

    clauses: list[str] = []
    first = sentences[0]
    if len(first) > 22 and ", " in first:
        parts = first.split(", ")
        head = parts[0]
        idx = 1
        while idx < len(parts) and len(head) < 10:
            head = f"{head}, {parts[idx]}"
            idx += 1
        tail = ", ".join(parts[idx:]).strip()
        if head and tail:
            clauses.append(head + ",")
            clauses.append(tail)
        else:
            clauses.append(first)
    else:
        clauses.append(first)

    for s in sentences[1:]:
        if clauses and len(clauses[-1]) + len(s) + 1 <= 110 and len(clauses) >= 2:
            clauses[-1] = f"{clauses[-1]} {s}"
        else:
            clauses.append(s)

    return clauses


def _prewarm_tts_cache():
    """Làm nóng kết nối HTTPS Keep-Alive / Edge WebSocket và cache sẵn các câu chào mẫu ở background."""
    try:
        _synthesize_google_tts("Xin chào")
    except Exception:
        pass
    user_name = get_user_profile().get("name") or "bạn"
    samples = [
        ("hoaimy", f"Chào {user_name}! Mình là Wisp, trợ lý AI cá nhân của bạn. Hôm nay bạn cần mình tra cứu hay trò chuyện gì nào?"),
        ("namminh", f"Chào {user_name}! Mình là Wisp, trợ lý AI cá nhân của bạn. Bạn cần hỗ trợ gì hôm nay cứ nói nhé."),
        ("hoaimy_fast", f"Chào {user_name}! Mình là Wisp, sẵn sàng hỗ trợ bạn ngay đây."),
        ("namminh_deep", f"Chào {user_name}, mình là Wisp. Cần hỏi thời tiết hay viết code cứ bảo mình nhé."),
        ("ava_multilingual", f"Chào {user_name}! Mình là Wisp, trợ lý AI song ngữ của bạn."),
    ]
    for vid, text in samples:
        try:
            generate_vietnamese_tts_mp3(text, vid)
        except Exception:
            pass


if "unittest" not in sys.modules and not any("test" in arg for arg in sys.argv):
    threading.Thread(target=_prewarm_tts_cache, daemon=True).start()


@app.route("/", methods=["GET"])
def index():
    """Trả về trang giao diện chat chính."""
    return render_template("index.html", greeting=GREETING, profile=get_user_profile())


@app.route("/api/voices", methods=["GET"])
def api_voices():
    """Trả về danh sách các giọng đọc tiếng Việt được hỗ trợ."""
    voices_list = [
        {"id": p["id"], "name": p["name"], "engine": p["engine"]}
        for p in VOICE_PROFILES.values()
    ]
    return jsonify({"voices": voices_list, "default": DEFAULT_VOICE_ID}), 200


@app.route("/api/tts", methods=["POST", "GET"])
def api_tts():
    """Sinh luồng âm thanh MP3 giọng đọc tiếng Việt chuẩn xác."""
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        raw_text = str(data.get("text", "") or "")
        voice_id = str(data.get("voice", DEFAULT_VOICE_ID) or DEFAULT_VOICE_ID)
    else:
        raw_text = str(request.args.get("text", "") or "")
        voice_id = str(request.args.get("voice", DEFAULT_VOICE_ID) or DEFAULT_VOICE_ID)

    mp3_bytes = generate_vietnamese_tts_mp3(raw_text, voice_id)
    if not mp3_bytes:
        return Response(status=204)

    return Response(
        mp3_bytes,
        mimetype="audio/mpeg",
        headers={
            "Cache-Control": "no-store",
            "Content-Length": str(len(mp3_bytes)),
        },
    )


@app.route("/api/profile", methods=["GET", "POST", "DELETE"])
def api_profile():
    """Đọc, cập nhật hoặc khôi phục mặc định Hồ sơ cá nhân của người dùng (Assistant Memory)."""
    try:
        if request.method == "DELETE":
            prof = reset_user_profile()
            return jsonify({"status": "ok", "profile": prof}), 200
        if request.method == "POST":
            data = request.get_json(silent=True) or {}
            prof = update_user_profile(data if isinstance(data, dict) else {})
            return jsonify({"status": "ok", "profile": prof}), 200
        return jsonify({"status": "ok", "profile": get_user_profile()}), 200
    except Exception:
        return jsonify({"status": "error", "profile": get_user_profile()}), 200


@app.route("/api/history", methods=["GET", "POST", "DELETE"])
def api_history():
    """Đọc, đồng bộ khôi phục (sau F5) hoặc xóa toàn bộ lịch sử hội thoại giữa UI và Backend."""
    try:
        if request.method == "DELETE":
            bot.clear_chat_history()
            return jsonify({"ok": True, "message": "Đã xóa toàn bộ lịch sử hội thoại"}), 200
        if request.method == "POST":
            data = request.get_json(silent=True) or {}
            items = data.get("history") if isinstance(data, dict) else []
            replace_if_empty = bool(data.get("replace_if_empty", True)) if isinstance(data, dict) else True
            count = restore_chat_history(items if isinstance(items, list) else [], replace_only_if_empty=replace_if_empty)
            return jsonify({"ok": True, "count": count, "history": get_chat_history()}), 200
        return jsonify({"ok": True, "history": get_chat_history()}), 200
    except Exception:
        return jsonify({"ok": False, "message": "⚠️ Có lỗi khi đồng bộ lịch sử hội thoại."}), 200


@app.route("/api/chat", methods=["POST"])
def api_chat():
    """Nhận tin nhắn từ người dùng, chuẩn hóa tiếng Việt và trả về câu trả lời của bot."""
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            data = {}
        raw_msg = data.get("message", "")
        message = raw_msg if isinstance(raw_msg, str) else str(raw_msg or "")
        prefs = data.get("prefs") if isinstance(data.get("prefs"), dict) else None
        normalized = normalize_vietnamese_input(message)
        reply = get_reply(normalized, prefs=prefs)
        return jsonify({"reply": reply, "normalized": normalized, "profile": get_user_profile()}), 200
    except Exception:
        return jsonify({"reply": "⚠️ Có lỗi gì đó, thử lại nhé."}), 200


@app.route("/api/chat/stream", methods=["POST"])
def api_chat_stream():
    """
    Stream NDJSON thời gian thực qua `queue.Queue` phi chặn (Zero Head-of-Line Blocking):
    - Đẩy từng token chữ về trình duyệt NGAY LẬP TỨC (0ms chờ).
    - Đồng thời tổng hợp TTS song song trên `_BG_LOOP` và bắn gói `{"type": "audio", "b64": ...}`
      vào hàng đợi ngay khoảnh khắc mỗi vế câu vừa tạo xong MP3 (không cần chờ câu sau hay chờ `done`).
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    raw_msg = data.get("message", "")
    message = raw_msg if isinstance(raw_msg, str) else str(raw_msg or "")
    voice_id = str(data.get("voice", DEFAULT_VOICE_ID) or DEFAULT_VOICE_ID)
    speak_enabled = bool(data.get("speak", False))
    prefs = data.get("prefs") if isinstance(data.get("prefs"), dict) else None
    normalized = normalize_vietnamese_input(message)

    def _submit_tts_future(clause_text: str, fast_first: bool = False):
        return asyncio.run_coroutine_threadsafe(
            _async_generate_tts(clause_text, voice_id, fast_first=fast_first),
            _BG_LOOP,
        )

    def _encode_audio_event(mp3_bytes: bytes) -> str | None:
        if not mp3_bytes:
            return None
        b64 = base64.b64encode(mp3_bytes).decode("ascii")
        return json.dumps({"type": "audio", "b64": b64}, ensure_ascii=False) + "\n"

    def generate():
        out_q: queue.Queue[str | None] = queue.Queue()
        stop_event = threading.Event()
        audio_lock = threading.RLock()
        ordered_futures: list = []
        next_audio_idx = 0

        def _flush_ready_audio_locked() -> None:
            nonlocal next_audio_idx
            if stop_event.is_set():
                return
            while next_audio_idx < len(ordered_futures):
                fut = ordered_futures[next_audio_idx]
                if not fut.done():
                    break
                next_audio_idx += 1
                try:
                    mp3_bytes = fut.result(timeout=0.05)
                    evt_line = _encode_audio_event(mp3_bytes)
                    if evt_line:
                        out_q.put(evt_line)
                except BaseException:
                    pass

        def _enqueue_tts_clause(clause_text: str) -> None:
            if stop_event.is_set():
                return
            clean_c = clause_text.strip()
            if not clean_c:
                return
            with audio_lock:
                is_first = len(ordered_futures) == 0
                fut = _submit_tts_future(clean_c, fast_first=is_first)
                ordered_futures.append(fut)

            def _on_fut_done(_f) -> None:
                with audio_lock:
                    _flush_ready_audio_locked()

            fut.add_done_callback(_on_fut_done)

        def _producer_worker() -> None:
            nonlocal next_audio_idx
            accumulated_text = ""
            spoken_char_idx = 0
            fast_path_handled = False
            in_tts_code_fence = False
            code_lead_in_spoken = False

            # Bắt vế câu đầu tiên cực sớm (>= 6 ký tự có dấu ngắt câu hoặc dấu phẩy)
            first_clause_re = re.compile(
                r"^([\s\S]{6,}?(?<![0-9])[.!?。;:\n]+(?:\s+|$)|[\s\S]{6,}?,\s+)"
            )
            next_sentence_re = re.compile(
                r"^([\s\S]{22,}?(?<![0-9])[.!?。;\n]+(?:\s+|$)|[\s\S]{34,}?,\s+)"
            )

            try:
                out_q.put(json.dumps({"type": "normalized", "text": normalized}, ensure_ascii=False) + "\n")
                for item in stream_reply(normalized, prefs=prefs, is_voice=speak_enabled):
                    if stop_event.is_set():
                        break
                    if isinstance(item, dict):
                        item_type = item.get("type")

                        if item_type == "done":
                            # Trước khi gửi `done`, tổng hợp nốt phần văn bản còn lại và đợi mọi gói audio xả hết
                            if in_tts_code_fence:
                                spoken_char_idx = len(accumulated_text)
                            if speak_enabled and not fast_path_handled and spoken_char_idx < len(accumulated_text):
                                rem = accumulated_text[spoken_char_idx:].strip()
                                spoken_char_idx = len(accumulated_text)
                                if rem:
                                    for c in _split_tts_clauses(rem):
                                        if stop_event.is_set():
                                            break
                                        _enqueue_tts_clause(c)

                            if speak_enabled:
                                while True:
                                    if stop_event.is_set():
                                        break
                                    with audio_lock:
                                        _flush_ready_audio_locked()
                                        if next_audio_idx >= len(ordered_futures):
                                            break
                                        pending_fut = ordered_futures[next_audio_idx]
                                    try:
                                        pending_fut.result(timeout=2.8)
                                    except BaseException:
                                        with audio_lock:
                                            if next_audio_idx < len(ordered_futures) and ordered_futures[next_audio_idx] is pending_fut:
                                                next_audio_idx += 1

                            out_q.put(json.dumps(item, ensure_ascii=False) + "\n")
                            continue

                        # Đẩy ngay lập tức token / state xuống trình duyệt (0ms head-of-line blocking!)
                        out_q.put(json.dumps(item, ensure_ascii=False) + "\n")

                        if speak_enabled and item_type == "token":
                            delta_str = str(item.get("delta") or "")
                            if (
                                not accumulated_text
                                and re.match(
                                    r"^(?:🌤️|🕐|📅|👋|🤖|🧹|⚠️|Gõ gì đó|Thời tiết hiện tại|Dự báo ngày mai|Bây giờ là|Bây giờ ở|Hôm nay là|Wisp đã xóa)",
                                    delta_str.strip(),
                                )
                            ):
                                accumulated_text = delta_str
                                spoken_char_idx = len(delta_str)
                                fast_path_handled = True
                                for c in _split_tts_clauses(delta_str):
                                    if stop_event.is_set():
                                        break
                                    _enqueue_tts_clause(c)
                                continue

                            if not fast_path_handled:
                                accumulated_text += delta_str
                                fence_count = delta_str.count("```")
                                if in_tts_code_fence:
                                    if fence_count > 0:
                                        # Closing code fence!
                                        in_tts_code_fence = False
                                        code_lead_in_spoken = False
                                        spoken_char_idx = len(accumulated_text)
                                    else:
                                        # Bỏ qua dòng code không đẩy vào hàng đợi TTS
                                        spoken_char_idx = len(accumulated_text)
                                        continue
                                elif fence_count > 0:
                                    if fence_count % 2 == 1:
                                        # Bắt đầu code fence!
                                        in_tts_code_fence = True
                                        if not code_lead_in_spoken:
                                            _enqueue_tts_clause("Đã có đoạn mã nguồn trên màn hình.")
                                            code_lead_in_spoken = True
                                        spoken_char_idx = len(accumulated_text)
                                        continue

                                while True:
                                    if stop_event.is_set():
                                        break
                                    unspoken = accumulated_text[spoken_char_idx:]
                                    with audio_lock:
                                        has_any_audio = len(ordered_futures) > 0
                                    pattern = first_clause_re if not has_any_audio else next_sentence_re
                                    m = pattern.match(unspoken)
                                    if not m or not m.group(1):
                                        break
                                    chunk_str = m.group(1)
                                    spoken_char_idx += len(chunk_str)
                                    _enqueue_tts_clause(chunk_str)

                    elif isinstance(item, str):
                        out_q.put(item if item.endswith("\n") else item + "\n")
            except Exception:
                if not stop_event.is_set():
                    out_q.put(
                        json.dumps(
                            {"type": "token", "delta": "⚠️ Có lỗi gì đó, thử lại nhé."},
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    out_q.put(json.dumps({"type": "done"}, ensure_ascii=False) + "\n")
            finally:
                out_q.put(None)

        threading.Thread(target=_producer_worker, daemon=True).start()

        try:
            while True:
                line = out_q.get()
                if line is None:
                    break
                yield line
        finally:
            stop_event.set()
            with audio_lock:
                pending = [fut for fut in ordered_futures if not fut.done()]
            for fut in pending:
                try:
                    fut.cancel()
                except Exception:
                    pass

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
