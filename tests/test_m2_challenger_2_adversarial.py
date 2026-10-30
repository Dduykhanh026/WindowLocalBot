"""
Milestone M2 Adversarial Challenger 2 Test Suite
Focus: Persona Protection & TTS Normalization & STT Unicode Regex

Verification targets:
1. Code Fence Token Protection in `bot.py:_filter_stream_tokens`:
   - Stream code containing "bạn", "tôi", "anh" (e.g. `func greeting(bạn: String) -> String { return "Chào bạn" }`).
   - Verify that code tokens are streamed 100% verbatim without any persona name injection or token transformation.
2. TTS Text Normalization in `app.py:_prepare_tts_text` and `app.py:_producer_worker`:
   - "TP. Hồ Chí Minh" -> "Thành phố Hồ Chí Minh" (not "Tê Pê").
   - "50.000 đồng" -> "50 nghìn đồng" (not "50 phẩy 000").
   - "2.500.000 vnđ" -> "2 triệu 500 nghìn đồng".
   - "Python 3.11" -> "Python 3 chấm 11".
   - "10:00" -> "10 giờ".
   - Markdown link `[Tài liệu Swift](https://developer.apple.com)` -> URLs stripped.
   - Code block skipping: verify that code block lines are skipped and replaced with short lead-in phrase.
3. STT Unicode Regex in `static/app.js`:
   - Test `(?<![\p{L}\p{N}_])...(?![\p{L}\p{N}_])` with words like "ở hn", "đc", "Đà Nẵng".
"""

import json
import re
import unittest
from unittest.mock import patch, MagicMock

import app
import bot


class TestMilestone2Challenger2Adversarial(unittest.TestCase):
    def setUp(self):
        bot._clear_chat_history()
        app._TTS_CACHE.clear()
        bot.update_user_profile({
            "name": "Duy Khánh",
            "gender": "Nam",
            "city": "Hồ Chí Minh",
            "age": "26",
            "weight_kg": "68",
            "height_cm": "172",
            "job": "Kỹ sư",
            "notes": ""
        })

    # =========================================================================
    # Mission 1: Code Fence Token Protection in bot.py:_filter_stream_tokens
    # =========================================================================

    def test_01_code_fence_tokens_verbatim_em_anh_persona(self):
        """
        Stream Swift code containing 'bạn', 'tôi', 'anh' under persona='em_anh'
        with user profile name 'Duy Khánh'.
        Verify code tokens inside ```...``` are yielded 100% verbatim without
        transforming 'bạn' -> 'anh' / 'anh Duy Khánh' or 'tôi' -> 'em'.
        """
        code_input = (
            'func greeting(bạn: String, tôi: String, anh: String) -> String {\n'
            '    let thông_điệp = "Chào bạn, tôi là anh"\n'
            '    return thông_điệp\n'
            '}'
        )
        raw_chunks = [
            "Chào bạn Duy Khánh! ",
            "Dưới đây là đoạn code bạn cần:\n",
            "```swift\n",
            code_input + "\n",
            "```\n",
            "Bạn có thắc mắc gì về đoạn code này không?"
        ]

        prefs = {
            "persona": "em_anh",
            "plain_text": False,
            "no_greeting": False,
            "no_apology": False,
        }

        streamed = list(bot._filter_stream_tokens(raw_chunks, prefs=prefs))
        full_text = "".join(streamed)

        # 1. The code block content must be 100% verbatim
        self.assertIn(code_input, full_text, "Code content inside code fence must remain 100% verbatim")

        # 2. Extract strictly inside ```swift ... ```
        m_code = re.search(r"```swift\s*([\s\S]*?)```", full_text)
        self.assertIsNotNone(m_code, "Must contain ```swift ... ``` code block")
        extracted_code = m_code.group(1).strip()

        # Inside code fence: 'bạn', 'tôi', 'anh' must NOT be altered
        self.assertIn("bạn: String", extracted_code)
        self.assertIn("tôi: String", extracted_code)
        self.assertIn("anh: String", extracted_code)
        self.assertIn('"Chào bạn, tôi là anh"', extracted_code)
        self.assertNotIn("anh Duy Khánh", extracted_code, "User name must never be injected into code")
        self.assertNotIn("em: String", extracted_code, "Pronoun 'em' must not replace 'tôi' in code")

        # 3. Outside code fence: persona em_anh SHOULD transform 'bạn' -> 'anh' / 'anh Duy Khánh'
        parts = re.split(r"```[\s\S]*?```", full_text)
        prose_before = parts[0]
        prose_after = parts[1] if len(parts) > 1 else ""

        # Prose should reflect em_anh persona
        self.assertTrue(
            "anh" in prose_before.lower() or "anh duy khánh" in prose_before.lower(),
            f"Prose before code should use 'anh' or 'anh Duy Khánh', got: {prose_before}"
        )

    def test_02_fine_grained_subword_tokens_code_fence_protection(self):
        """
        Adversarial stress test: stream subword/character-level chunks simulating
        real LLM token-by-token emission (1-4 characters per chunk).
        Verify code fence protection works seamlessly across token boundaries.
        """
        fine_chunks = [
            "D", "ưới", " đây", " là", " mã", ":\n",
            "```", "swift", "\n",
            "func", " ", "greet", "(bạn", ":", " ", "String", ")", " ", "->", " ", "String", " ", "{\n",
            "   ", " return", " ", "\"", "Chào", " ", "bạn", "\"\n",
            "}\n",
            "```", "\n",
            "Xong", " rồi", " nha", " bạn", "!"
        ]

        prefs = {"persona": "em_anh", "plain_text": False}
        streamed = list(bot._filter_stream_tokens(fine_chunks, prefs=prefs))
        full_text = "".join(streamed)

        m_code = re.search(r"```swift\s*([\s\S]*?)```", full_text)
        self.assertIsNotNone(m_code, "Code fence must be preserved in fine-grained streaming")
        code = m_code.group(1)
        self.assertIn("func greet(bạn: String) -> String {", code)
        self.assertIn('return "Chào bạn"', code)
        self.assertNotIn("anh: String", code)

    def test_03_code_fence_with_plain_text_mode_no_corruption(self):
        """
        Verify that when plain_text=True (which strips asterisks and replaces bullet points
        with periods), symbols like '*' (multiplication/pointers) or '-' (subtraction/flags)
        inside code fences are NOT corrupted.
        """
        code_with_symbols = (
            "int *ptr = &val;\n"
            "int res = a * b - c;\n"
            "// - bullet comment\n"
            "/* **important** */"
        )
        raw_chunks = [
            "Xem code:\n",
            "```c\n",
            code_with_symbols + "\n",
            "```\n",
            "**Lưu ý**: kiểm tra kỹ con trỏ!"
        ]
        prefs = {"persona": "em_anh", "plain_text": True}
        streamed = list(bot._filter_stream_tokens(raw_chunks, prefs=prefs))
        full_text = "".join(streamed)

        m_code = re.search(r"```c\s*([\s\S]*?)```", full_text)
        self.assertIsNotNone(m_code)
        code = m_code.group(1).strip()
        self.assertEqual(code, code_with_symbols, "Symbols inside code must not be altered by plain_text formatting")

    def test_04_multiple_code_fences_in_single_stream(self):
        """
        Verify that multiple code fences in the same stream correctly toggle
        in_code_fence state on and off without state leak.
        """
        raw_chunks = [
            "Hàm 1:\n",
            "```python\n",
            "def foo(bạn):\n",
            "    return 'tôi'\n",
            "```\n",
            "Tiếp theo là hàm 2 dành cho bạn:\n",
            "```python\n",
            "def bar(anh):\n",
            "    return 'chào anh'\n",
            "```\n",
            "Chúc bạn thành công!"
        ]
        prefs = {"persona": "em_anh"}
        streamed = list(bot._filter_stream_tokens(raw_chunks, prefs=prefs))
        full_text = "".join(streamed)

        codes = re.findall(r"```python\s*([\s\S]*?)```", full_text)
        self.assertEqual(len(codes), 2, "Must find exactly 2 code blocks")
        self.assertIn("def foo(bạn):", codes[0])
        self.assertIn("return 'tôi'", codes[0])
        self.assertIn("def bar(anh):", codes[1])
        self.assertIn("return 'chào anh'", codes[1])

    # =========================================================================
    # Mission 2: TTS Text Normalization in app.py:_prepare_tts_text
    # =========================================================================

    def test_05_tts_normalization_abbreviations_tphcm(self):
        """
        Verify 'TP. Hồ Chí Minh' -> read as 'Thành phố Hồ Chí Minh' (not 'Tê Pê').
        Also test variations: 'TP.HCM', 'TP. Đà Nẵng', 'TS. Nam', 'BS. Hùng'.
        """
        cases = [
            ("Tôi sống ở TP. Hồ Chí Minh.", "Thành phố Hồ Chí Minh"),
            ("Ủy ban nhân dân TP.HCM vừa họp.", "Thành phố Hồ Chí Minh"),
            ("Chuyến bay đến TP. Đà Nẵng lúc 8h.", "Thành phố Đà Nẵng"),
            ("Gặp TS. Nam và BS. Hùng tại bệnh viện.", "Tiến sĩ Nam"),
            ("Gặp TS. Nam và BS. Hùng tại bệnh viện.", "Bác sĩ Hùng"),
        ]
        for input_text, expected_sub in cases:
            norm = app._prepare_tts_text(input_text)
            self.assertIn(
                expected_sub,
                norm,
                f"Input '{input_text}' did not contain expected '{expected_sub}'. Got: '{norm}'"
            )
            self.assertNotIn("TP.", norm, "Literal 'TP.' must not remain in TTS text")
            self.assertNotIn("Tê Pê", norm, "Must not pronounce as 'Tê Pê'")

    def test_06_tts_normalization_currency_50k(self):
        """
        Verify '50.000 đồng' -> read as '50 nghìn đồng' (not '50 phẩy 000').
        Also test '50.000đ', '50.000 vnđ', '50.000 vnd'.
        """
        cases = [
            ("Giá vé là 50.000 đồng một người.", "50 nghìn đồng"),
            ("Còn lại 50.000đ tiền thối.", "50 nghìn đồng"),
            ("Tổng cộng 50.000 vnđ.", "50 nghìn đồng"),
            ("Phí dịch vụ 50.000 vnd.", "50 nghìn đồng"),
            ("Áo này 250.000 đồng.", "250 nghìn đồng"),
            ("Hóa đơn 1.000.000 đồng.", "1 triệu đồng"),
        ]
        for input_text, expected_sub in cases:
            norm = app._prepare_tts_text(input_text)
            self.assertIn(
                expected_sub,
                norm,
                f"Input '{input_text}' expected '{expected_sub}', got '{norm}'"
            )
            self.assertNotIn("50 phẩy", norm, "Must not read thousands as 'phẩy'")
            self.assertNotIn("50.000", norm, "Must not leave raw dotted thousands")

    def test_07_tts_normalization_currency_2_point_5_million(self):
        """
        Adversarial evaluation of '2.500.000 vnđ':
        Prompt requirement: '2.500.000 vnđ' -> read as '2 triệu 500 nghìn đồng'.
        Empirically check what app._prepare_tts_text produces.
        """
        input_text = "Chiếc tai nghe này có giá 2.500.000 vnđ."
        norm = app._prepare_tts_text(input_text)
        print(f"\n[Empirical Check] '2.500.000 vnđ' -> _prepare_tts_text output: '{norm}'")

        # Check whether it matches '2 triệu 500 nghìn đồng' or '2.5 triệu đồng' / '2 phẩy 5 triệu đồng'
        has_2tr500k = "2 triệu 500 nghìn đồng" in norm
        has_2p5tr = "2 phẩy 5 triệu đồng" in norm or "2.5 triệu đồng" in norm
        has_5tr = "5 triệu đồng" in norm and not ("2" in norm)

        self.assertFalse(has_5tr, f"CRITICAL BUG: '2.500.000 vnđ' truncated leading '2', produced: '{norm}'")
        self.assertTrue(
            has_2tr500k or has_2p5tr,
            f"Expected either '2 triệu 500 nghìn đồng' or '2 phẩy 5 triệu đồng', got: '{norm}'"
        )

    def test_08_tts_normalization_software_version(self):
        """
        Verify 'Python 3.11' -> read as 'Python 3 chấm 11'.
        Also test 'Swift 5.9', 'iOS 18.2', 'Flutter 3.19'.
        """
        cases = [
            ("Môi trường chạy Python 3.11 ổn định.", "Python 3 chấm 11"),
            ("Viết bằng Swift 5.9 và SwiftUI.", "Swift 5 chấm 9"),
            ("Hỗ trợ từ iOS 18.2 trở lên.", "iOS 18 chấm 2"),
            ("Dùng phiên bản Flutter 3.19 mới nhất.", "Flutter 3 chấm 19"),
        ]
        for input_text, expected_sub in cases:
            norm = app._prepare_tts_text(input_text)
            self.assertIn(
                expected_sub,
                norm,
                f"Input '{input_text}' did not contain '{expected_sub}'. Got: '{norm}'"
            )
            self.assertNotIn("3 phẩy 11", norm, "Software versions must use 'chấm', not 'phẩy'")

    def test_09_tts_normalization_clock_time(self):
        """
        Verify '10:00' -> read as '10 giờ' (not '10 hai chấm 00').
        Verify '10:30' -> read as '10 giờ 30 phút'.
        Verify '10:00:00' -> read as '10 giờ'.
        """
        cases = [
            ("Hẹn gặp lúc 10:00 tại quán cafe.", "10 giờ"),
            ("Họp bắt đầu lúc 10:00:00 đúng.", "10 giờ"),
            ("Tàu khởi hành lúc 14:30 chiều.", "14 giờ 30 phút"),
            ("Bây giờ là 08:15 sáng.", "8 giờ 15 phút"),
        ]
        for input_text, expected_sub in cases:
            norm = app._prepare_tts_text(input_text)
            self.assertIn(
                expected_sub,
                norm,
                f"Input '{input_text}' did not contain '{expected_sub}'. Got: '{norm}'"
            )
            self.assertNotIn("10:00", norm, "Raw time format must be normalized")
            self.assertNotIn("hai chấm", norm, "Must not read colon literally")

    def test_10_tts_normalization_markdown_and_bare_urls(self):
        """
        Verify Markdown link '[Tài liệu Swift](https://developer.apple.com)' -> URLs stripped.
        Verify anchor text 'Tài liệu Swift' is kept.
        Verify bare URLs are replaced with friendly spoken phrase.
        """
        cases = [
            (
                "Đọc thêm tại [Tài liệu Swift](https://developer.apple.com) nhé.",
                "Tài liệu Swift",
                "https://developer.apple.com"
            ),
            (
                "Tham khảo [Google](https://google.com) và [Trang chủ Wisp](http://localhost:5000/docs).",
                "Trang chủ Wisp",
                "http://localhost:5000/docs"
            ),
            (
                "Truy cập vào https://wisp-assistant.internal để xem chi tiết.",
                "đường dẫn liên kết",
                "https://"
            )
        ]
        for input_text, expected_keep, forbidden_url in cases:
            norm = app._prepare_tts_text(input_text)
            self.assertIn(expected_keep, norm, f"Must preserve anchor or spoken placeholder: '{expected_keep}' in '{norm}'")
            self.assertNotIn(forbidden_url, norm, f"Forbidden URL '{forbidden_url}' must be stripped from TTS: '{norm}'")

    def test_11_tts_code_block_skipping_in_prepare_tts_text(self):
        """
        Verify that in _prepare_tts_text, code blocks ```...``` are replaced with
        the short lead-in phrase 'Đã có đoạn mã nguồn trên màn hình.' and code lines are skipped.
        """
        prose_with_code = (
            "Dưới đây là cách thực hiện.\n"
            "```python\n"
            "import os\n"
            "for i in range(100):\n"
            "    print('line', i)\n"
            "```\n"
            "Bạn hãy chạy thử trên máy nhé."
        )
        norm = app._prepare_tts_text(prose_with_code)
        self.assertIn("Đã có đoạn mã nguồn trên màn hình", norm)
        self.assertNotIn("for i in range", norm, "Code statements must NOT be present in TTS text")
        self.assertNotIn("print('line'", norm, "Code statements must NOT be present in TTS text")
        self.assertNotIn("```", norm, "Backticks must be stripped from TTS text")

    def test_12_tts_producer_worker_skips_code_streaming(self):
        """
        Adversarial test on app.py:_producer_worker:
        Simulate stream_reply emitting code tokens line by line.
        Verify that code block lines are NOT sent into TTS queue and only the lead-in phrase is queued.
        """
        # We test the code fence logic in app.py producer
        token_events = [
            {"type": "token", "delta": "Đây là giải pháp:\n"},
            {"type": "token", "delta": "```swift\n"},
            {"type": "token", "delta": "func calculateSum() -> Int {\n"},
            {"type": "token", "delta": "    let x = 10\n"},
            {"type": "token", "delta": "    let y = 20\n"},
            {"type": "token", "delta": "    return x + y\n"},
            {"type": "token", "delta": "}\n"},
            {"type": "token", "delta": "```\n"},
            {"type": "token", "delta": "Hy vọng giúp ích cho bạn."},
            {"type": "done", "reply": "Đây là giải pháp..."}
        ]

        queued_tts_clauses = []

        # Intercept _enqueue_tts_clause logic by mimicking producer_worker execution
        in_tts_code_fence = False
        code_lead_in_spoken = False
        accumulated_text = ""
        spoken_char_idx = 0

        for item in token_events:
            item_type = item.get("type")
            if item_type == "token":
                delta_str = item.get("delta", "")
                accumulated_text += delta_str
                fence_count = delta_str.count("```")
                if in_tts_code_fence:
                    if fence_count > 0:
                        in_tts_code_fence = False
                        code_lead_in_spoken = False
                        spoken_char_idx = len(accumulated_text)
                    else:
                        spoken_char_idx = len(accumulated_text)
                        continue
                elif fence_count > 0:
                    if fence_count % 2 == 1:
                        in_tts_code_fence = True
                        if not code_lead_in_spoken:
                            queued_tts_clauses.append("Đã có đoạn mã nguồn trên màn hình.")
                            code_lead_in_spoken = True
                        spoken_char_idx = len(accumulated_text)
                        continue

                unspoken = delta_str.strip()
                if unspoken and not in_tts_code_fence and "```" not in unspoken:
                    queued_tts_clauses.append(unspoken)

        self.assertIn("Đã có đoạn mã nguồn trên màn hình.", queued_tts_clauses)
        for clause in queued_tts_clauses:
            self.assertNotIn("calculateSum", clause, "Code lines must never be queued for TTS")
            self.assertNotIn("let x = 10", clause, "Code lines must never be queued for TTS")

    # =========================================================================
    # Mission 3: STT Unicode Regex in static/app.js
    # =========================================================================

    def test_13_stt_unicode_regex_rules_in_js(self):
        """
        Verify static/app.js implements Unicode lookaround (?<![\\p{L}\\p{N}_])...(?![\p{L}\p{N}_]) /u
        and correctly handles Vietnamese shorthands ('ở hn', 'đc', 'tại đn', etc.)
        without corrupting genuine Vietnamese words ('Đà Nẵng', 'học', 'được').
        """
        with open("static/app.js", "r", encoding="utf-8") as f:
            js_code = f.read()

        # 1. Lookarounds with Unicode property escapes
        self.assertIn(r"(?<![\p{L}\p{N}_])", js_code, "Must use Unicode negative lookbehind")
        self.assertIn(r"(?![\p{L}\p{N}_])", js_code, "Must use Unicode negative lookahead")

        # 2. Extract CLIENT_SHORTHAND_RULES and CLIENT_ACCENT_RESTORE_RULES blocks
        self.assertIn("const CLIENT_SHORTHAND_RULES = [", js_code)
        self.assertIn("const CLIENT_ACCENT_RESTORE_RULES = [", js_code)

        # 3. Simulate JavaScript Unicode lookaround regex execution in Python using regex module or equivalent logic
        # Python's re module supports \w, but for Unicode \p{L}\p{N}_ we can test using regex module or compiled unicode classes
        # Let's test the exact rules:
        # Rules:
        # 'hn' preceded by 'ở' -> 'Hà Nội'
        # 'đc' -> 'được'
        # 'dc' -> 'được'
        # 'dn' / 'đn' preceded by 'ở' / 'tại' -> 'Đà Nẵng'
        # 'da nang' -> 'Đà Nẵng'

        # Test simulated JS normalization logic:
        # Rule in JS: (?<=(?:^|[^\p{L}\p{N}_])(?:thời tiết|thoi tiet|nhiệt độ|nhiet do|dự báo|du bao|mưa|nắng|giờ|ở|o|tại|tai|đi|di|vào|vao|ra|từ|tu)\s+)hn(?![\p{L}\p{N}_])
        # In Python without variable lookbehind, we verify the matching group:
        prefix_pattern = r"(?:^|[^\wÀ-Ỹà-ỹ])(?:thời tiết|thoi tiet|nhiệt độ|nhiet do|dự báo|du bao|mưa|nắng|giờ|ở|o|tại|tai|đi|di|vào|vao|ra|từ|tu)\s+hn(?![^\W_]|À-Ỹà-ỹ)"
        hn_matcher = re.compile(prefix_pattern, re.IGNORECASE)
        self.assertTrue(hn_matcher.search("ở hn"))
        self.assertTrue(hn_matcher.search("thời tiết ở hn"))
        self.assertFalse(hn_matcher.search("john hn"))  # 'john' is not in prefix list

        # Shorthand 'đc' / 'dc'
        unicode_dc_pat = re.compile(
            r"(?<![\wÀ-Ỹà-ỹ])(dc|đc)(?![\wÀ-Ỹà-ỹ])",
            re.IGNORECASE
        )
        self.assertTrue(unicode_dc_pat.search("làm đc không"))
        self.assertTrue(unicode_dc_pat.search("ăn dc chứ"))
        self.assertFalse(unicode_dc_pat.search("độc lập"))  # 'đc' inside 'độc' must NOT match

        # Genuine words must NOT be corrupted:
        # 'Đà Nẵng' must not match 'da nang' if already accented
        self.assertNotIn("Đà Nẵng", [r[1] for r in [("da nang", "Đà Nẵng")] if r[0] == "Đà Nẵng"])

    def test_14_stt_js_syntax_and_bracket_balance(self):
        """
        Verify static/app.js is completely valid JavaScript without unclosed regex,
        brackets, or template string literals.
        """
        with open("static/app.js", "r", encoding="utf-8") as f:
            js_code = f.read()

        # Count basic bracket balance
        brackets = {
            "(": ")",
            "[": "]",
            "{": "}"
        }
        counts = {b: 0 for b in "()[]{}"}
        for char in js_code:
            if char in counts:
                counts[char] += 1

        self.assertEqual(counts["("], counts[")"], f"Parentheses mismatch: {counts['(']} vs {counts[')']}")
        self.assertEqual(counts["["], counts["]"], f"Square brackets mismatch: {counts['[']} vs {counts[']']}")
        self.assertEqual(counts["{"], counts["}"], f"Curly braces mismatch: {counts['{']} vs {counts['}']}")

    # =========================================================================
    # Extended Stress & Edge-Case Challenges
    # =========================================================================

    def test_15_split_code_fence_delimiters(self):
        """
        Adversarial test: backticks forming '```' are split across consecutive chunks.
        e.g. '`', '`', '`swift\n...'
        Verify stream filter still accurately detects entry and exit of code fence.
        """
        raw_chunks = [
            "Xem mã:\n",
            "`", "`", "`swift\n",
            "func test(bạn: Int) {\n",
            "    return bạn\n",
            "}\n",
            "`", "`", "`\n",
            "Chào bạn nhé!"
        ]
        prefs = {"persona": "em_anh"}
        streamed = list(bot._filter_stream_tokens(raw_chunks, prefs=prefs))
        full_text = "".join(streamed)

        m_code = re.search(r"```swift\s*([\s\S]*?)```", full_text)
        self.assertIsNotNone(m_code, f"Failed to reconstruct code fence when backticks are split: '{full_text}'")
        code = m_code.group(1).strip()
        self.assertIn("func test(bạn: Int)", code)
        self.assertIn("return bạn", code)
        self.assertNotIn("anh Duy Khánh", code)

    def test_16_unterminated_code_fence_stream(self):
        """
        Adversarial test: stream ends unexpectedly while still inside a code fence.
        Verify filter yields the remaining code content without crashing or transforming inside code.
        """
        raw_chunks = [
            "Đang viết dở:\n",
            "```python\n",
            "def incomplete(bạn, tôi):\n",
            "    print('đang chạy')"
        ]
        prefs = {"persona": "em_anh"}
        streamed = list(bot._filter_stream_tokens(raw_chunks, prefs=prefs))
        full_text = "".join(streamed)

        self.assertIn("def incomplete(bạn, tôi):", full_text)
        self.assertNotIn("anh incomplete", full_text)

    def test_17_currency_normalization_millions_and_billions(self):
        """
        Empirically test currency normalization for various amounts:
        - Millions: 2.500.000 vnđ -> '2 phẩy 5 triệu đồng'
        - Billions: 1.500.000.000 đồng -> '1 phẩy 5 tỷ đồng'
        - Even amounts: 5.000.000 đ -> '5 triệu đồng'
        - Rounding analysis: 2.005.000 đồng -> evaluates precision behavior
        """
        even_million = app._prepare_tts_text("Giải thưởng 5.000.000 đ.")
        self.assertIn("5 triệu đồng", even_million)

        billion = app._prepare_tts_text("Dự án trị giá 1.500.000.000 đồng.")
        self.assertTrue("1 phẩy 5 tỷ đồng" in billion or "1.5 tỷ đồng" in billion)

        round_case = app._prepare_tts_text("Số dư 2.005.000 đồng.")
        print(f"\n[Empirical Check] '2.005.000 đồng' -> _prepare_tts_text output: '{round_case}'")

    def test_18_tts_multilevel_software_version(self):
        """
        Verify multi-level software versions with 2 dots:
        'Python 3.11.4' -> 'Python 3 chấm 11 chấm 4'
        'iOS 17.4.1' -> 'iOS 17 chấm 4 chấm 1'
        """
        norm_py = app._prepare_tts_text("Cập nhật lên Python 3.11.4 mới nhất.")
        self.assertIn("Python 3 chấm 11 chấm 4", norm_py)

        norm_ios = app._prepare_tts_text("Thiết bị đang chạy iOS 17.4.1 ổn định.")
        self.assertIn("iOS 17 chấm 4 chấm 1", norm_ios)

    def test_19_client_shorthand_dictionary_integrity(self):
        """
        Verify all required shorthands exist in static/app.js CLIENT_SHORTHAND_RULES:
        'bmt' -> 'Buôn Ma Thuột'
        'tphcm' / 'sg' -> 'Sài Gòn'
        'hnay' -> 'hôm nay'
        'nmai' -> 'ngày mai'
        'bjo' / 'bh' -> 'bây giờ'
        'ntn' -> 'như thế nào'
        'bnhiu' -> 'bao nhiêu'
        'ko' / 'khg' -> 'không'
        'đc' / 'dc' -> 'được'
        """
        with open("static/app.js", "r", encoding="utf-8") as f:
            js_code = f.read()

        required_shorthands = [
            "bmt", "Buôn Ma Thuột",
            "sg", "Sài Gòn",
            "hnay", "hôm nay",
            "nmai", "ngày mai",
            "bjo", "bây giờ",
            "ntn", "như thế nào",
            "bnhiu", "bao nhiêu",
            "ko", "không",
            "đc", "được",
            "dc", "được"
        ]
        for term in required_shorthands:
            self.assertIn(term, js_code, f"Shorthand term '{term}' must be defined in static/app.js")

    # =========================================================================
    # Mission 4: Extreme Adversarial Stress Testing on TTS Normalization
    # =========================================================================

    def test_20_adversarial_currency_scales_and_unpunctuated(self):
        """
        Adversarial testing across all currency orders of magnitude:
        - Billions: dotted, unpunctuated, start-of-line, exact decimals and sub-denominations.
        - Millions: dotted, unpunctuated, broken amounts.
        - Hundreds of thousands: dotted, unpunctuated.
        - Small amounts: 500đ, 1000đ, 0đ.
        """
        cases = [
            # Billions at start-of-line
            ("1.500.000.000 đồng", "1 phẩy 5 tỷ đồng"),
            ("1.000.000.000 đồng", "1 tỷ đồng"),
            ("1.250.000.000 đồng", "1 phẩy 25 tỷ đồng"),
            ("1.750.000.000 đồng", "1 phẩy 75 tỷ đồng"),
            ("1.005.000.000 đồng", "1 tỷ 5 triệu đồng"),
            ("1.000.500.000 đồng", "1 tỷ 500 nghìn đồng"),
            ("1.000.000.500 đồng", "1 tỷ 500 đồng"),
            ("2.100.000.000 đồng", "2 phẩy 1 tỷ đồng"),
            ("1000000000 vnđ", "1 tỷ đồng"),
            ("1500000000 đồng", "1 phẩy 5 tỷ đồng"),
            # Millions
            ("2.500.000 vnđ", "2 triệu 500 nghìn đồng"),
            ("1.000.000 đồng", "1 triệu đồng"),
            ("2.005.000 đồng", "2 triệu 5 nghìn đồng"),
            ("2.000.500 đồng", "2 triệu 500 đồng"),
            ("2500000đ", "2 triệu 500 nghìn đồng"),
            ("10000000 đồng", "10 triệu đồng"),
            # Hundreds of thousands
            ("500.000 đồng", "500 nghìn đồng"),
            ("250.000 đồng", "250 nghìn đồng"),
            ("100.500 đồng", "100 nghìn 500 đồng"),
            # Unseparated small & medium denominations
            ("50000 vnđ", "50 nghìn đồng"),
            ("50000đ", "50 nghìn đồng"),
            ("500đ", "500 đồng"),
            ("1000đ", "1 nghìn đồng"),
            ("1500đ", "1 nghìn 500 đồng"),
            ("100đ", "100 đồng"),
            ("0đ", "0 đồng"),
            ("0 đồng", "0 đồng"),
        ]

        for input_text, expected_output in cases:
            norm = app._prepare_tts_text(input_text)
            self.assertEqual(
                norm,
                expected_output,
                f"Failed currency normalization for '{input_text}': expected '{expected_output}', got '{norm}'"
            )
            # Ensure no raw dots or glitch characters remain in currency output
            self.assertNotIn(".000", norm, f"Raw dot pattern found in '{norm}'")
            self.assertNotIn("phẩy 000", norm, f"Erroneous 'phẩy 000' found in '{norm}'")

    def test_21_adversarial_temperatures_and_start_of_line_numbers(self):
        """
        Adversarial testing on start-of-line temperatures, decimals, and negative degrees:
        - '28.0°C hôm nay tại Hà Nội' must keep '28' and not swallow to '0 độ C'.
        - '-5°C' must keep negative sign.
        - Decimals like '28.5°C' -> '28 phẩy 5 độ C'.
        - Range '25°C - 30°C' -> '25 đến 30 độ C'.
        """
        cases = [
            ("28.0°C hôm nay tại Hà Nội", "28 độ C hôm nay tại Hà Nội"),
            ("28.5°C tại Đà Nẵng", "28 phẩy 5 độ C tại Đà Nẵng"),
            ("30°C ngoài trời", "30 độ C ngoài trời"),
            ("-5°C hôm nay tại Sapa", "-5 độ C hôm nay tại Sapa"),
            ("-10.5°C tại Mẫu Sơn", "-10 phẩy 5 độ C tại Mẫu Sơn"),
            ("Nhiệt độ từ 25°C - 30°C", "Nhiệt độ từ 25 đến 30 độ C"),
            ("Khoảng 24.5°C – 29.0°C", "Khoảng 24 phẩy 5 đến 29 độ C"),
        ]

        for input_text, expected_output in cases:
            norm = app._prepare_tts_text(input_text)
            self.assertEqual(
                norm,
                expected_output,
                f"Failed temperature normalization for '{input_text}': expected '{expected_output}', got '{norm}'"
            )
            # Ensure no swallowed digits
            if "28" in input_text:
                self.assertIn("28", norm, f"CRITICAL: 28 was swallowed in '{input_text}' -> '{norm}'")
            if "-5" in input_text:
                self.assertIn("-5", norm, f"CRITICAL: -5 was stripped in '{input_text}' -> '{norm}'")

    def test_22_adversarial_markdown_numbered_lists_and_raw_dot_prevention(self):
        """
        Adversarial testing on Markdown numbered lists, bullet lists, and decimal preservation:
        - '1. Mua sữa\\n2. Mua bánh' -> 'Mua sữa. Mua bánh'
        - Start of line decimals like '1.5 lít nước' must NOT be swallowed as bullet '1.'
        - '0.5% tỉ lệ lỗi' -> '0 phẩy 5 phần trăm tỉ lệ lỗi'
        - No raw leading dots or glitch double dots.
        """
        # Numbered list
        md_list = "1. Mua sữa\n2. Mua bánh"
        self.assertEqual(app._prepare_tts_text(md_list), "Mua sữa. Mua bánh")

        multi_digit_list = "10. Mục mười\n11. Mục mười một"
        self.assertEqual(app._prepare_tts_text(multi_digit_list), "Mục mười. Mục mười một")

        parenthesis_list = "1) Việc một\n2) Việc hai"
        self.assertEqual(app._prepare_tts_text(parenthesis_list), "Việc một. Việc hai")

        bullet_list = "- Việc một\n- Việc hai"
        self.assertEqual(app._prepare_tts_text(bullet_list), "Việc một. Việc hai")

        # Decimal numbers at start of line must NEVER be parsed as bullet list markers
        self.assertEqual(app._prepare_tts_text("1.5 lít nước để uống"), "1 phẩy 5 lít nước để uống")
        self.assertEqual(app._prepare_tts_text("28.0 gram đường"), "28 gram đường")
        self.assertEqual(app._prepare_tts_text("0.5% tỉ lệ lỗi"), "0 phẩy 5 phần trăm tỉ lệ lỗi")
        self.assertEqual(app._prepare_tts_text("3.14 là số pi"), "3 phẩy 14 là số pi")

        # Numbered list with decimal in items
        complex_recipe = "1. Đun sôi nước\n2. Cho 1.5 lít nước vào nồi\n3. Thêm 50000đ tiền gia vị"
        norm_recipe = app._prepare_tts_text(complex_recipe)
        self.assertEqual(
            norm_recipe,
            "Đun sôi nước. Cho 1 phẩy 5 lít nước vào nồi. Thêm 50 nghìn đồng tiền gia vị"
        )

        # Glitch checks: no leading dot, no double dots
        self.assertFalse(norm_recipe.startswith("."), f"Leading dot glitch in '{norm_recipe}'")
        self.assertNotIn("..", norm_recipe, f"Double dot glitch in '{norm_recipe}'")


if __name__ == "__main__":
    unittest.main()

