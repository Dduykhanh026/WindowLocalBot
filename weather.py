"""
Thời tiết — dùng Open-Meteo (miễn phí, không cần API key).
Docs: https://open-meteo.com/en/docs
"""
import json
import unicodedata
import urllib.request
import urllib.parse
import urllib.error

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Map tên gõ không dấu -> tên để query Geocoding
CITY_ALIASES = {
    "sai gon": "Ho Chi Minh",
    "saigon": "Ho Chi Minh",
    "ho chi minh": "Ho Chi Minh",
    "tp hcm": "Ho Chi Minh",
    "hcm": "Ho Chi Minh",
    "tp ho chi minh": "Ho Chi Minh",
    "ha noi": "Ha Noi",
    "hanoi": "Ha Noi",
    "da nang": "Da Nang",
    "hue": "Hue",
    "can tho": "Can Tho",
    "hai phong": "Hai Phong",
    "nha trang": "Nha Trang",
    "da lat": "Da Lat",
    "vung tau": "Vung Tau",
    "phu quoc": "Phu Quoc",
    "con dao": "Con Dao",
    "bien hoa": "Bien Hoa",
    "binh duong": "Thu Dau Mot",
    "quy nhon": "Quy Nhon",
    "phan thiet": "Phan Thiet",
    "long xuyen": "Long Xuyen",
    "rach gia": "Rach Gia",
    "buon ma thuot": "Buon Ma Thuot",
    "pleiku": "Pleiku",
    "vinh": "Vinh",
    "thanh hoa": "Thanh Hoa",
    "nam dinh": "Nam Dinh",
    "thai binh": "Thai Binh",
    "ha long": "Ha Long",
    "lao cai": "Lao Cai",
    "sapa": "Sa Pa",
    "ha giang": "Ha Giang",
    "dien bien": "Dien Bien Phu",
    "son la": "Son La",
    "bac ninh": "Bac Ninh",
    "bac giang": "Bac Giang",
    "hai duong": "Hai Duong",
    "hung yen": "Hung Yen",
    "ninh binh": "Ninh Binh",
    "ha tinh": "Ha Tinh",
    "quang binh": "Quang Binh",
    "quang tri": "Quang Tri",
    "quang nam": "Hoi An",
    "quang ngai": "Quang Ngai",
    "kon tum": "Kon Tum",
    "gia lai": "Pleiku",
    "dak lak": "Buon Ma Thuot",
    "dak nong": "Gia Nghia",
    "lam dong": "Da Lat",
    "ninh thuan": "Phan Rang",
    "binh thuan": "Phan Thiet",
    "binh dinh": "Quy Nhon",
    "phu yen": "Tuy Hoa",
    "khanh hoa": "Nha Trang",
    "tay ninh": "Tay Ninh",
    "binh phuoc": "Dong Xoai",
    "dong nai": "Bien Hoa",
    "ba ria": "Ba Ria",
    "long an": "Tan An",
    "tien giang": "My Tho",
    "ben tre": "Ben Tre",
    "tra vinh": "Tra Vinh",
    "vinh long": "Vinh Long",
    "dong thap": "Cao Lanh",
    "an giang": "Long Xuyen",
    "kien giang": "Rach Gia",
    "hau giang": "Vi Thanh",
    "soc trang": "Soc Trang",
    "bac lieu": "Bac Lieu",
    "ca mau": "Ca Mau",
    # Quốc tế hay hỏi
    "tokyo": "Tokyo",
    "seoul": "Seoul",
    "bangkok": "Bangkok",
    "singapore": "Singapore",
    "london": "London",
    "paris": "Paris",
    "new york": "New York",
    "sydney": "Sydney",
}

WMO_TEXT = {
    0: "Trời quang đãng ☀️",
    1: "Trời quang, có chút mây ☀️",
    2: "Mây rải rác ⛅",
    3: "Nhiều mây ☁️",
    45: "Sương mù 🌫️",
    48: "Sương mù đóng băng 🌫️",
    51: "Mưa phùn nhẹ 🌦️",
    53: "Mưa phùn 🌦️",
    55: "Mưa phùn dày 🌦️",
    56: "Mưa phùn đóng băng nhẹ 🌦️",
    57: "Mưa phùn đóng băng 🌦️",
    61: "Mưa nhẹ 🌧️",
    63: "Mưa vừa 🌧️",
    65: "Mưa to 🌧️",
    66: "Mưa đóng băng nhẹ 🌧️",
    67: "Mưa đóng băng 🌧️",
    71: "Tuyết nhẹ ❄️",
    73: "Tuyết vừa ❄️",
    75: "Tuyết to ❄️",
    77: "Hạt tuyết ❄️",
    80: "Mưa rào nhẹ 🌦️",
    81: "Mưa rào vừa 🌦️",
    82: "Mưa rào to 🌦️",
    85: "Mưa tuyết nhẹ ❄️",
    86: "Mưa tuyết to ❄️",
    95: "Giông bão ⛈️",
    96: "Giông kèm mưa đá ⛈️",
    99: "Giông kèm mưa đá to ⛈️",
}


def _norm_city(s: str) -> str:
    """Chuẩn hoá tên thành phố để tra alias: bỏ dấu + đ->d."""
    s = s.replace("Đ", "D").replace("đ", "d").replace("Ð", "D").replace("ð", "d")
    s = "".join(c for c in unicodedata.normalize("NFD", s)
                if unicodedata.category(c) != "Mn")
    return s.lower().strip()


def _clean_name(s: str | None) -> str:
    """Sửa lỗi Open-Meteo trả 'Ð' (Eth) thay vì 'Đ' cho Đà Lạt / Đà Nẵng..."""
    if not s:
        return ""
    return s.replace("Ð", "Đ").replace("ð", "đ")


def _place_label(loc: dict) -> str:
    name = _clean_name(loc.get("name"))
    admin1 = _clean_name(loc.get("admin1"))
    # So sánh không dấu + không khoảng trắng: "Ha Noi" == "Hanoi" -> không lặp
    if not admin1 or _norm_city(admin1).replace(" ", "") == _norm_city(name).replace(" ", ""):
        return name
    return f"{name}, {admin1}"


_GEO_CACHE: dict[str, dict] = {}
_JSON_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 180.0  # Cache 3 phút để giảm tải và tránh lỗi 503 từ Open-Meteo


def _get_json(url: str, timeout: int = 10, retries: int = 5) -> dict:
    import time
    now = time.time()
    cached = _JSON_CACHE.get(url)
    if cached and (now - cached[0]) < _CACHE_TTL:
        return cached[1]

    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "windows-local-bot/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                _JSON_CACHE[url] = (time.time(), data)
                return data
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(0.4 * (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(0.3)
                continue
            raise
    if cached:
        return cached[1]
    raise last_err  # type: ignore[misc]


def geocode(city: str) -> dict | None:
    """Tìm tọa độ + tên chuẩn của 1 thành phố. Trả None nếu không tìm thấy."""
    q = CITY_ALIASES.get(_norm_city(city), city.strip())
    cache_key = q.lower()
    if cache_key in _GEO_CACHE:
        return _GEO_CACHE[cache_key]
    params = urllib.parse.urlencode(
        {"name": q, "count": 1, "language": "vi", "format": "json"}
    )
    data = _get_json(f"{GEO_URL}?{params}")
    results = data.get("results") or []
    if not results:
        return None
    r = results[0]
    loc = {
        "name": r.get("name"),
        "admin1": r.get("admin1"),
        "country": r.get("country"),
        "latitude": r["latitude"],
        "longitude": r["longitude"],
        "timezone": r.get("timezone", "auto"),
    }
    _GEO_CACHE[cache_key] = loc
    return loc


def get_current_weather(city: str) -> str:
    """Chuỗi thời tiết hiện tại, đã format tiếng Việt."""
    try:
        loc = geocode(city)
    except urllib.error.HTTPError:
        return "⚠️ Lỗi khi tra cứu địa điểm, thử tên thành phố khác nhé."
    except (urllib.error.URLError, TimeoutError, OSError):
        return "⚠️ Không có mạng rồi — kiểm tra lại Wi-Fi rồi thử lại nhé."
    except Exception:
        return "⚠️ Lỗi khi tra cứu địa điểm, thử tên thành phố khác nhé."
    if not loc:
        return f"⚠️ Không tìm thấy địa điểm “{city}”. Thử gõ tên thành phố khác, vd: Hà Nội, Đà Nẵng, Đà Lạt."

    try:
        params = urllib.parse.urlencode(
            {
                "latitude": loc["latitude"],
                "longitude": loc["longitude"],
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
                "timezone": "auto",
            }
        )
        data = _get_json(f"{FORECAST_URL}?{params}")
        cur = data["current"]
        desc = WMO_TEXT.get(cur.get("weather_code"), "Không rõ")
        place = _place_label(loc)
        return (
            f"🌤️ Thời tiết hiện tại tại {place}:\n"
            f"• {desc}\n"
            f"• Nhiệt độ: {cur['temperature_2m']}°C (cảm giác như {cur['apparent_temperature']}°C)\n"
            f"• Độ ẩm: {cur['relative_humidity_2m']}%\n"
            f"• Gió: {cur['wind_speed_10m']} km/h"
        )
    except urllib.error.HTTPError:
        return "⚠️ Lỗi khi lấy thời tiết, thử lại sau 1 chút nhé."
    except (urllib.error.URLError, TimeoutError, OSError):
        return "⚠️ Không có mạng rồi — kiểm tra lại Wi-Fi rồi thử lại nhé."
    except Exception:
        return "⚠️ Lỗi khi lấy thời tiết, thử lại sau 1 chút nhé."


def get_tomorrow_weather(city: str) -> str:
    """Dự báo ngày mai (min/max + tóm tắt)."""
    try:
        loc = geocode(city)
    except urllib.error.HTTPError:
        return "⚠️ Lỗi khi tra cứu địa điểm, thử tên thành phố khác nhé."
    except (urllib.error.URLError, TimeoutError, OSError):
        return "⚠️ Không có mạng rồi — kiểm tra lại Wi-Fi rồi thử lại nhé."
    except Exception:
        return "⚠️ Lỗi khi tra cứu địa điểm, thử tên thành phố khác nhé."
    if not loc:
        return f"⚠️ Không tìm thấy địa điểm “{city}”. Thử gõ tên thành phố khác nhé."
    try:
        params = urllib.parse.urlencode(
            {
                "latitude": loc["latitude"],
                "longitude": loc["longitude"],
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "timezone": "auto",
                "forecast_days": 2,
            }
        )
        data = _get_json(f"{FORECAST_URL}?{params}")
        d = data["daily"]
        # index 1 = ngày mai
        code = d["weather_code"][1]
        tmax = d["temperature_2m_max"][1]
        tmin = d["temperature_2m_min"][1]
        rain = d.get("precipitation_probability_max", [None, None])[1]
        desc = WMO_TEXT.get(code, "Không rõ")
        place = _place_label(loc)
        rain_txt = f"\n• Khả năng mưa: {rain}%" if rain is not None else ""
        return (
            f"🌤️ Dự báo ngày mai tại {place}:\n"
            f"• {desc}\n"
            f"• Nhiệt độ: {tmin}°C – {tmax}°C{rain_txt}"
        )
    except urllib.error.HTTPError:
        return "⚠️ Lỗi khi lấy dự báo, thử lại sau 1 chút nhé."
    except (urllib.error.URLError, TimeoutError, OSError):
        return "⚠️ Không có mạng rồi — kiểm tra lại Wi-Fi rồi thử lại nhé."
    except Exception:
        return "⚠️ Lỗi khi lấy dự báo, thử lại sau 1 chút nhé."
