"""
Automated Verification Suite for Milestone M2 (Natural Language & Intent Protection)
Tests:
1. F11, F12, F13: STT Endpointing & Vietnamese Regex patterns (lookarounds, trailing speech, keyword dampening).
2. F14 & F15: Dynamic Persona & Gender Field in profile, system prompt, templates, and voice previews.
3. F16 & F17: Code Fence Protection in streaming and no truncation on done event.
4. F18: World Clock Intent Fix (disambiguate single-word country collisions).
5. F19: Weather Topic Substring Fix (whole-word bounded regex, eliminate false positives like 'tính năng', 'mất ngủ').
6. F20: Temperature Query & Alias Fix ('bao nhiêu độ', 'mấy độ', 'phu quoc', advisory word sanitization).
7. F21: Code-First Intent Precision (exclude generic verbs 'viết', 'tạo', 'mẫu', 'ham'; require programming keywords).
8. F22: Auto-Learn Edge Cases (require 1st person pronoun; reject 3rd party facts and temporary vacations; clean names).
9. F23 & F24: TTS Code Block Skipping & Text Normalization (abbreviations, currency, software versions, times, markdown links).
"""
import json
import re
import unittest
from unittest.mock import patch, MagicMock

import app
import bot
import weather


class TestMilestone2Verification(unittest.TestCase):
    def setUp(self):
        bot._clear_chat_history()
        app._TTS_CACHE.clear()

    # --------------------------------------------------------------------------
    # F14 & F15: Dynamic Persona & Gender Field
    # --------------------------------------------------------------------------
    def test_01_user_profile_contains_gender(self):
        """Verify user_profile.json, DEFAULT_USER_PROFILE, and _normalize_profile_dict include gender."""
        profile = bot.get_user_profile()
        self.assertIn("gender", profile)
        self.assertIn(profile["gender"], ["Nam", "Nữ", "Khác"])

        self.assertIn("gender", bot.DEFAULT_USER_PROFILE)
        self.assertEqual(bot.DEFAULT_USER_PROFILE["gender"], "Nam")

        norm = bot._normalize_profile_dict({})
        self.assertIn("gender", norm)
        self.assertEqual(norm["gender"], "Nam")

    def test_02_dynamic_persona_and_prompt_builder(self):
        """Verify system prompt and profile prompt dynamically adapt to user profile without hardcoded 'Nguyên'."""
        # Set profile to a different user
        custom_profile = {
            "name": "Duy Khánh",
            "gender": "Nam",
            "city": "Đà Nẵng",
            "job": "Kỹ sư AI",
            "interests": "Machine Learning",
            "birth_year": "2000",
            "weight": "65kg",
            "notes": ""
        }
        with patch.object(bot, "get_user_profile", return_value=custom_profile):
            prompt_str = bot._build_user_profile_prompt()
            self.assertIn("Duy Khánh", prompt_str)
            self.assertIn("- Giới tính: Nam", prompt_str)
            self.assertNotIn("Nguyên", prompt_str)

            sys_prompt = bot._build_system_prompt()
            self.assertIn("Duy Khánh", sys_prompt)
            self.assertNotIn("Nguyên", sys_prompt)

    def test_03_persona_text_fixes_preserve_technical_terms(self):
        """Verify persona fixes do not replace 'bạn' inside technical terms or code."""
        # In minh_nguyen / minh_ban mode, standalone 'bạn' is preserved
        prefs = {"persona": "minh_nguyen"}
        sample_text = "Hàm func get_reply(bạn: String) trả về thông tin cho bạn."
        fixed = bot._apply_persona_text_fixes(sample_text, prefs)
        # Standalone 'bạn' should NOT be forcibly replaced with user name in technical sentence
        self.assertIn("bạn", fixed)

    def test_04_templates_render_profile_gender_and_name(self):
        """Verify index.html contains gender select field and SSR profile rendering."""
        with open("templates/index.html", "r", encoding="utf-8") as f:
            html = f.read()

        self.assertIn('id="profGenderInput"', html)
        self.assertIn('value="Nam"', html)
        self.assertIn('value="Nữ"', html)
        self.assertIn('value="Khác"', html)
        self.assertIn("profileTopName", html)
        self.assertIn("profile.name", html)

    def test_05_voice_preview_samples_refer_to_wisp(self):
        """Verify voice preview samples refer to assistant as Wisp and do not use personal actor names."""
        for voice_id, text in app.VOICE_PREVIEW_SAMPLES.items():
            self.assertIn("Wisp", text, f"Voice {voice_id} sample should mention Wisp")
            self.assertNotIn("Minh Khôi", text)
            self.assertNotIn("Linh Đan", text)
            self.assertNotIn("Chào Nguyên", text)

    # --------------------------------------------------------------------------
    # F16 & F17: Code Fence Protection & Stream Jitter Fix
    # --------------------------------------------------------------------------
    def test_06_code_fence_protection_in_stream_tokens(self):
        """Verify code blocks are passed verbatim without persona modifications during streaming."""
        # Generate raw chunks that simulate a Markdown code fence
        raw_chunks = [
            "Dưới đây là mã nguồn:\n",
            "```swift\n",
            "func checkUser(bạn: String) -> Bool {\n",
            "    return bạn == \"admin\"\n",
            "}\n",
            "```\n",
            "Hy vọng đoạn mã giúp ích cho bạn!"
        ]
        prefs = {"persona": "em_anh"}
        filtered = list(bot._filter_stream_tokens(raw_chunks, prefs=prefs))
        full_output = "".join(filtered)

        # Inside the code block, 'bạn' must be untouched
        self.assertIn("func checkUser(bạn: String) -> Bool", full_output)
        self.assertIn("return bạn == \"admin\"", full_output)

    def test_07_clean_reply_by_prefs_no_stream_truncation(self):
        """Verify _clean_reply_by_prefs does not truncate streamed reply on is_fast_path=False."""
        long_reply = (
            "Chào bạn! Đây là câu trả lời đầu tiên rất chi tiết. "
            "Đây là câu thứ hai phân tích vấn đề một cách kỹ lưỡng. "
            "Đây là câu thứ ba hướng dẫn bạn cách xử lý cụ thể. "
            "Đây là câu thứ tư bổ sung các lưu ý quan trọng khi triển khai. "
            "Chúc bạn một ngày làm việc hiệu quả và thành công!"
        )
        prefs = {"style": "concise"}
        cleaned = bot._clean_reply_by_prefs(long_reply, prefs=prefs, is_fast_path=False)
        # Should not be truncated to 2 sentences because is_fast_path is False (streaming AI response)
        self.assertEqual(cleaned, long_reply.strip())

    # --------------------------------------------------------------------------
    # F18: World Clock Intent Fix
    # --------------------------------------------------------------------------
    def test_08_world_clock_single_word_collision_avoidance(self):
        """Verify world clock does not falsely trigger on pronouns/adjectives like 'anh', 'nhat', 'my'."""
        single_keys = ["anh", "nhat", "my", "duc", "uc", "sing"]
        for k in single_keys:
            self.assertNotIn(k, bot.WORLD_TZ, f"Single-word key '{k}' must not be in WORLD_TZ")

        # False positive queries should NOT match world clock
        false_queries = [
            "Bây giờ anh đói bụng quá",
            "Món nào ở đây ngon nhất?",
            "Anh có khỏe không?",
            "Nghệ thuật nước ta rất phong phú",
            "Học tiếng Anh ở đâu tốt?"
        ]
        for q in false_queries:
            reply = bot._match_fast_command(q)
            # Must not return world clock string
            if reply:
                self.assertNotIn("Giờ hiện tại ở", reply, f"Query '{q}' should NOT match world clock")

        # True world clock queries SHOULD match
        true_queries = [
            "Mấy giờ ở Tokyo?",
            "Giờ ở London là mấy giờ?",
            "Bây giờ là mấy giờ ở Nhật Bản?",
            "Giờ hiện tại ở Nước Mỹ"
        ]
        for q in true_queries:
            reply = bot._match_fast_command(q)
            self.assertIsNotNone(reply, f"Query '{q}' should match world clock")
            self.assertIn("Bây giờ ở", reply)

    # --------------------------------------------------------------------------
    # F19: Weather Topic Substring Fix
    # --------------------------------------------------------------------------
    def test_09_weather_topic_regex_protection(self):
        """Verify _is_weather_topic does not falsely match words containing substrings like 'tính năng', 'mất ngủ'."""
        false_positives = [
            "SwiftUI có tính năng gì mới?",
            "Dạo này mình bị mất ngủ",
            "Đi mua sắm ở trung tâm thương mại",
            "Cần rèn luyện tính kiên nhẫn",
            "Mát-xa trị liệu đau lưng"
        ]
        for text in false_positives:
            norm = bot._norm(text)
            self.assertFalse(bot._is_weather_topic(text, norm), f"'{text}' should NOT be weather topic")

        true_weather = [
            "Thời tiết hôm nay thế nào?",
            "Hôm nay trời có mưa không?",
            "Ngày mai trời có nắng không?",
            "Dự báo thời tiết cuối tuần",
            "Trời hôm nay nhiều mây u ám"
        ]
        for text in true_weather:
            norm = bot._norm(text)
            self.assertTrue(bot._is_weather_topic(text, norm), f"'{text}' SHOULD be weather topic")

    # --------------------------------------------------------------------------
    # F20: Temperature Query & Alias Fix
    # --------------------------------------------------------------------------
    def test_10_temperature_command_detection_and_aliases(self):
        """Verify 'bao nhiêu độ', 'mấy độ' trigger weather command and Phu Quoc alias exists."""
        # 1. City alias
        self.assertIn("phu quoc", weather.CITY_ALIASES)
        self.assertEqual(weather.CITY_ALIASES["phu quoc"], "Phu Quoc")
        self.assertIn("con dao", weather.CITY_ALIASES)

        # 2. Temperature commands recognized
        temp_queries = [
            "Huế bao nhiêu độ?",
            "Sài Gòn mấy độ?",
            "Đà Nẵng hôm nay bao nhiêu độ",
            "nhiệt độ Hà Nội"
        ]
        for q in temp_queries:
            norm = bot._norm(q)
            self.assertTrue(bot._is_short_weather_command(norm), f"'{q}' should be short weather command")

        # 3. Advisory word sanitization in _extract_city_after
        extracted = bot._extract_city_after("thời tiết đà lạt mặc gì cho ấm", ["thoi tiet"])
        self.assertEqual(extracted, "da lat", "Advisory phrase 'mặc gì cho ấm' should be stripped from city name")

        extracted_temp = bot._extract_city_after("thời tiết huế bao nhiêu độ", ["thoi tiet"])
        self.assertEqual(extracted_temp, "hue", "Query phrase 'bao nhiêu độ' should be stripped from city name")

    # --------------------------------------------------------------------------
    # F21: Code-First Intent Precision
    # --------------------------------------------------------------------------
    def test_11_code_first_intent_precision(self):
        """Verify _is_code_request is not triggered by generic verbs like 'viết', 'tạo', 'mẫu', 'ham'."""
        generic_non_code = [
            "Gợi ý mẫu áo sơ mi công sở",
            "Viết lời chúc mừng sinh nhật bạn thân",
            "Tạo một kế hoạch tập luyện giảm cân",
            "Mình là người rất ham học hỏi",
            "Mẫu thực đơn ăn kiêng lành mạnh"
        ]
        for text in generic_non_code:
            self.assertFalse(bot._is_code_request(text), f"'{text}' should NOT trigger code request")

        programming_requests = [
            "Viết hàm Swift sắp xếp mảng",
            "Tạo class Flutter quản lý state giỏ hàng",
            "Gọi API bằng Python requests",
            "Viết đoạn code JavaScript debounce hàm tìm kiếm",
            "Giải thích thuật toán Binary Search"
        ]
        for text in programming_requests:
            self.assertTrue(bot._is_code_request(text), f"'{text}' SHOULD trigger code request")

    # --------------------------------------------------------------------------
    # F22: Auto-Learn Edge Cases
    # --------------------------------------------------------------------------
    def test_12_auto_learn_requires_first_person_subject(self):
        """Verify profile auto-update rejects 3rd-party facts and vacations, but accepts 1st-person updates."""
        from datetime import datetime
        # Reset profile
        bot.update_user_profile({
            "name": "Nguyên",
            "city": "Sài Gòn",
            "weight_kg": "",
            "height_cm": "",
            "age": "26"
        })

        # 1. 3rd-party luggage / facts: must NOT update weight or birth year
        bot.extract_and_update_profile_from_message("Vali nặng 32kg ký gửi máy bay")
        self.assertEqual(bot.get_user_profile().get("weight_kg"), "")

        bot.extract_and_update_profile_from_message("Steve Jobs sinh năm 1955 tại San Francisco")
        self.assertEqual(bot.get_user_profile().get("age"), "26")

        # 3rd-person pronoun and possessive filtering (M2 Remediation)
        bot.extract_and_update_profile_from_message("Anh ấy năm nay 40 tuổi rồi")
        self.assertEqual(bot.get_user_profile().get("age"), "26")

        bot.extract_and_update_profile_from_message("Anh ta 30 tuổi")
        self.assertEqual(bot.get_user_profile().get("age"), "26")

        bot.extract_and_update_profile_from_message("Mẹ mình năm nay 60 tuổi")
        self.assertEqual(bot.get_user_profile().get("age"), "26")

        bot.extract_and_update_profile_from_message("Bố tôi năm nay 65 tuổi")
        self.assertEqual(bot.get_user_profile().get("age"), "26")

        bot.extract_and_update_profile_from_message("Năm nay 40 tuổi")
        self.assertEqual(bot.get_user_profile().get("age"), "26")

        bot.extract_and_update_profile_from_message("Bạn mình nặng 80kg")
        self.assertEqual(bot.get_user_profile().get("weight_kg"), "")

        bot.extract_and_update_profile_from_message("Con mình nặng 20kg")
        self.assertEqual(bot.get_user_profile().get("weight_kg"), "")

        bot.extract_and_update_profile_from_message("Vợ mình cao 1m60")
        self.assertEqual(bot.get_user_profile().get("height_cm"), "")

        bot.extract_and_update_profile_from_message("Chồng mình cao 180cm")
        self.assertEqual(bot.get_user_profile().get("height_cm"), "")

        bot.extract_and_update_profile_from_message("Bố tôi sinh năm 1960")
        self.assertEqual(bot.get_user_profile().get("age"), "26")

        # Possessive 'của' filtering (Relatives, Friends, Pets, Possessions)
        bot.extract_and_update_profile_from_message("Bố của mình năm nay 60 tuổi")
        self.assertEqual(bot.get_user_profile().get("age"), "26")

        bot.extract_and_update_profile_from_message("Mẹ của tôi năm nay 55 tuổi")
        self.assertEqual(bot.get_user_profile().get("age"), "26")

        bot.extract_and_update_profile_from_message("Bố của mình sinh năm 1960")
        self.assertEqual(bot.get_user_profile().get("age"), "26")

        bot.extract_and_update_profile_from_message("Bạn của mình nặng 80kg")
        self.assertEqual(bot.get_user_profile().get("weight_kg"), "")

        bot.extract_and_update_profile_from_message("Vợ của mình cao 1m60")
        self.assertEqual(bot.get_user_profile().get("height_cm"), "")

        bot.extract_and_update_profile_from_message("Chồng của mình cao 180cm")
        self.assertEqual(bot.get_user_profile().get("height_cm"), "")

        bot.extract_and_update_profile_from_message("Xe của mình nặng 120kg")
        self.assertEqual(bot.get_user_profile().get("weight_kg"), "")

        bot.extract_and_update_profile_from_message("Chó của mình nặng 35kg")
        self.assertEqual(bot.get_user_profile().get("weight_kg"), "")

        bot.extract_and_update_profile_from_message("Vali của mình nặng 25kg")
        self.assertEqual(bot.get_user_profile().get("weight_kg"), "")

        bot.extract_and_update_profile_from_message("Cây mai của mình cao 1m80")
        self.assertEqual(bot.get_user_profile().get("height_cm"), "")

        bot.extract_and_update_profile_from_message("Con gái của mình năm nay 10 tuổi")
        self.assertEqual(bot.get_user_profile().get("age"), "26")

        # Compound relatives filtering
        bot.extract_and_update_profile_from_message("Con gái mình năm nay 10 tuổi")
        self.assertEqual(bot.get_user_profile().get("age"), "26")

        bot.extract_and_update_profile_from_message("Anh trai mình 30 tuổi")
        self.assertEqual(bot.get_user_profile().get("age"), "26")

        bot.extract_and_update_profile_from_message("Em gái mình cao 1m60")
        self.assertEqual(bot.get_user_profile().get("height_cm"), "")

        bot.extract_and_update_profile_from_message("Bạn thân mình nặng 75kg")
        self.assertEqual(bot.get_user_profile().get("weight_kg"), "")

        bot.extract_and_update_profile_from_message("Người yêu mình cao 1m65")
        self.assertEqual(bot.get_user_profile().get("height_cm"), "")

        bot.extract_and_update_profile_from_message("Bác mình năm nay 70 tuổi")
        self.assertEqual(bot.get_user_profile().get("age"), "26")

        bot.extract_and_update_profile_from_message("Sếp mình năm nay 50 tuổi")
        self.assertEqual(bot.get_user_profile().get("age"), "26")

        # Relative name and city protection
        bot.extract_and_update_profile_from_message("Con gái mình tên là Lan")
        self.assertEqual(bot.get_user_profile().get("name"), "Nguyên")

        bot.extract_and_update_profile_from_message("Bạn của mình tên là Tuấn")
        self.assertEqual(bot.get_user_profile().get("name"), "Nguyên")

        bot.extract_and_update_profile_from_message("Bạn của mình đang sống ở Hà Nội")
        self.assertEqual(bot.get_user_profile().get("city"), "Sài Gòn")

        bot.extract_and_update_profile_from_message("Bố mẹ mình đang ở Đà Nẵng")
        self.assertEqual(bot.get_user_profile().get("city"), "Sài Gòn")

        # 2. Temporary vacation: must NOT update living city
        bot.extract_and_update_profile_from_message("Mình đang ở Đà Lạt đi du lịch 3 ngày")
        self.assertEqual(bot.get_user_profile().get("city"), "Sài Gòn")

        # 3. Genuine 1st-person updates: MUST update profile
        bot.extract_and_update_profile_from_message("Mình nặng 68kg rồi")
        self.assertEqual(bot.get_user_profile().get("weight_kg"), "68")

        bot.extract_and_update_profile_from_message("Tôi sinh năm 2002 nhé")
        expected_age = str(datetime.now(bot.VN_TZ).year - 2002)
        self.assertEqual(bot.get_user_profile().get("age"), expected_age)

        bot.extract_and_update_profile_from_message("Mình năm nay 24 tuổi")
        self.assertEqual(bot.get_user_profile().get("age"), "24")

        bot.extract_and_update_profile_from_message("Năm nay mình 25 tuổi")
        self.assertEqual(bot.get_user_profile().get("age"), "25")

        bot.extract_and_update_profile_from_message("Mình cao 1m72")
        self.assertEqual(bot.get_user_profile().get("height_cm"), "172")

        bot.extract_and_update_profile_from_message("Chiều cao của mình là 1m75")
        self.assertEqual(bot.get_user_profile().get("height_cm"), "175")

        bot.extract_and_update_profile_from_message("Cân nặng của mình là 65kg")
        self.assertEqual(bot.get_user_profile().get("weight_kg"), "65")

        bot.extract_and_update_profile_from_message("Năm sinh của mình là 1998")
        expected_age_1998 = str(datetime.now(bot.VN_TZ).year - 1998)
        self.assertEqual(bot.get_user_profile().get("age"), expected_age_1998)

        bot.extract_and_update_profile_from_message("Tuổi của mình năm nay 25 tuổi")
        self.assertEqual(bot.get_user_profile().get("age"), "25")

        bot.extract_and_update_profile_from_message("Chào bạn mình 26 tuổi")
        self.assertEqual(bot.get_user_profile().get("age"), "26")

        # 4. Name extraction strips exclamations and respects 1-4 words
        bot.extract_and_update_profile_from_message("Tên mình là Khánh nha")
        self.assertEqual(bot.get_user_profile().get("name"), "Khánh")

        bot.extract_and_update_profile_from_message("Mình tên là Nguyễn Duy Khánh nhé!")
        self.assertEqual(bot.get_user_profile().get("name"), "Nguyễn Duy Khánh")

    # --------------------------------------------------------------------------
    # F23 & F24: TTS Code Block Skipping & Text Normalization
    # --------------------------------------------------------------------------
    def test_13_tts_text_normalization(self):
        """Verify _prepare_tts_text normalizes abbreviations, currency, versions, times, temperatures, and URLs."""
        # 1. Abbreviations
        raw_abbr = "Tôi sống ở TP. Hồ Chí Minh cùng TS. Nam."
        norm_abbr = app._prepare_tts_text(raw_abbr)
        self.assertIn("Thành phố Hồ Chí Minh", norm_abbr)
        self.assertIn("Tiến sĩ Nam", norm_abbr)

        # 2. Currency (in-sentence, start-of-line, and unpunctuated)
        raw_currency = "Món này giá 50.000 đồng, còn máy kia 2.500.000 vnđ."
        norm_currency = app._prepare_tts_text(raw_currency)
        self.assertIn("50 nghìn đồng", norm_currency)
        self.assertIn("2 triệu 500 nghìn đồng", norm_currency)

        # Additional currency precision tests
        self.assertIn("2 triệu 5 nghìn đồng", app._prepare_tts_text("Giá 2.005.000 đồng"))
        self.assertIn("2 triệu 500 đồng", app._prepare_tts_text("Giá 2.000.500 vnđ"))

        # Start-of-line currency (billions and millions)
        self.assertEqual(app._prepare_tts_text("1.500.000.000 đồng"), "1 phẩy 5 tỷ đồng")
        self.assertEqual(app._prepare_tts_text("2.005.000 đồng"), "2 triệu 5 nghìn đồng")
        self.assertEqual(app._prepare_tts_text("10.500.000.000 đồng"), "10 phẩy 5 tỷ đồng")
        self.assertEqual(app._prepare_tts_text("5.000.000.000 đ"), "5 tỷ đồng")

        # Unpunctuated currency
        self.assertEqual(app._prepare_tts_text("50000 vnđ"), "50 nghìn đồng")
        self.assertEqual(app._prepare_tts_text("50000đ"), "50 nghìn đồng")
        self.assertEqual(app._prepare_tts_text("500đ"), "500 đồng")

        # 3. Software versions
        raw_version = "Dự án chạy trên Python 3.11 và iOS 17.2 mượt mà."
        norm_version = app._prepare_tts_text(raw_version)
        self.assertIn("Python 3 chấm 11", norm_version)
        self.assertIn("iOS 17 chấm 2", norm_version)

        # 4. Times
        raw_time = "Cuộc họp diễn ra lúc 10:00 sáng và kết thúc lúc 14:30."
        norm_time = app._prepare_tts_text(raw_time)
        self.assertIn("10 giờ sáng", norm_time)
        self.assertIn("14 giờ 30 phút", norm_time)

        # 5. Start-of-line temperature and decimal preservation
        self.assertEqual(app._prepare_tts_text("28.0°C hôm nay tại Hà Nội"), "28 độ C hôm nay tại Hà Nội")
        self.assertEqual(app._prepare_tts_text("25.5°C tại Đà Lạt"), "25 phẩy 5 độ C tại Đà Lạt")
        self.assertEqual(app._prepare_tts_text("-5°C hôm nay tại Sapa"), "-5 độ C hôm nay tại Sapa")
        self.assertEqual(app._prepare_tts_text("3.14 là số pi"), "3 phẩy 14 là số pi")

        # 6. Markdown bullet lists preservation
        md_list = "1. Bước một\n2. Bước hai\n3. Bước ba"
        self.assertEqual(app._prepare_tts_text(md_list), "Bước một. Bước hai. Bước ba")

        dash_list = "- Điểm một\n- Điểm hai"
        self.assertEqual(app._prepare_tts_text(dash_list), "Điểm một. Điểm hai")

        # 7. Markdown URLs and bare URLs
        raw_url = "Xem tài liệu tại [Trang chủ](https://example.com/docs) hoặc vào https://wisp.ai ngay."
        norm_url = app._prepare_tts_text(raw_url)
        self.assertNotIn("https://", norm_url)
        self.assertIn("Trang chủ", norm_url)
        self.assertIn("liên kết", norm_url)

    # --------------------------------------------------------------------------
    # F11, F12, F13: Client-side Regex & Rules in static/app.js
    # --------------------------------------------------------------------------
    def test_14_static_app_js_regex_rules(self):
        """Verify static/app.js rules conform to M2 requirements (Unicode lookarounds, trailing speech, etc.)."""
        with open("static/app.js", "r", encoding="utf-8") as f:
            js_content = f.read()

        # 1. Trailing speech does NOT have 'không', 'chưa', 'đi', 'ơi'
        # Match the TRAILING_INCOMPLETE_SPEECH_RE line
        match_trailing = re.search(r"const TRAILING_INCOMPLETE_SPEECH_RE\s*=\s*(/.+/iu);", js_content)
        self.assertIsNotNone(match_trailing, "TRAILING_INCOMPLETE_SPEECH_RE should be defined with /iu")
        trailing_pattern = match_trailing.group(1)
        self.assertNotIn("|không|", trailing_pattern)
        self.assertNotIn("|chưa|", trailing_pattern)
        self.assertNotIn("|đi|", trailing_pattern)
        self.assertNotIn("|ơi|", trailing_pattern)

        # 2. Check Unicode lookaround in CLIENT_SHORTHAND_RULES and CLIENT_ACCENT_RESTORE_RULES
        self.assertIn(r"(?<![\p{L}\p{N}_])", js_content)
        self.assertIn(r"(?![\p{L}\p{N}_])", js_content)

        # 3. Check dampened keyword score
        self.assertIn("score += 0.8", js_content)
        self.assertNotIn("score += 5", js_content)

        # 4. Check silenceFinalMs in MIC_SENSITIVITY_PROFILES
        self.assertIn("silenceFinalMs: 1050", js_content)

        # 5. Check no false lastSoundDetectedMs on isFinal
        self.assertIn("if (hasInterim) {", js_content)


if __name__ == "__main__":
    unittest.main()
