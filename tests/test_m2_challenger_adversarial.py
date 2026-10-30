"""
Empirical Challenger Adversarial Test Suite for Milestone M2
(Natural Language & Intent Protection)

Author: Empirical Challenger 1
Focus: Adversarial stress testing of intent engines in `bot.py`
- World Clock disambiguation & trick sentences
- Weather Topic detection vs tricky homonyms & compound words
- Code-First trigger precision vs non-code verbs & conceptual queries
- Auto-Learn profile protection vs 3rd-party luggage, facts, vacations, and age leaks
"""
import unittest
from datetime import datetime
from unittest.mock import patch

import app
import bot
import weather


class TestMilestone2AdversarialChallenger(unittest.TestCase):
    def setUp(self):
        bot._clear_chat_history()
        app._TTS_CACHE.clear()
        # Reset profile before each test
        bot.update_user_profile({
            "name": "Khánh",
            "gender": "Nam",
            "city": "Sài Gòn",
            "age": "25",
            "weight_kg": "65",
            "height_cm": "170",
            "job": "Kỹ sư",
            "interests": "Lập trình",
            "notes": ""
        })

    # =========================================================================
    # 1. World Clock: Mission Requirements & Adversarial Trick Sentences
    # =========================================================================
    def test_01_world_clock_trick_sentences(self):
        """Stress-test world clock against tricky Vietnamese sentences."""
        # False positives: MUST NOT trigger world clock (must not return string with 'Bây giờ ở')
        trick_negatives = [
            # Mission requirements:
            ("Bây giờ anh đói bụng quá", "London"),
            ("Bây giờ món nào ngon nhất?", "Tokyo"),
            ("Tôi yêu nước Mỹ thuật", "New York"),
            # Additional trick sentences:
            ("Anh ơi bây giờ mấy giờ", "London"),
            ("Món ăn nước Pháp ngon quá", "Paris"),
            ("Xe hơi của nước Đức rất bền", "Berlin"),
            ("Tôi thích xem phim nước Mỹ", "New York"),
            ("Đi du lịch nước Úc hết bao nhiêu tiền", "Sydney"),
            ("Nước Anh có gì đẹp?", "London"),
            ("Thế giới này thật tươi đẹp", "Bây giờ ở"),
            ("Bây giờ anh phải đi ngủ rồi", "London"),
            ("Nhất định tôi sẽ ghé thăm bạn", "Tokyo"),
            ("Đức tính trung thực là điều đáng quý", "Berlin"),
            ("Mỹ vị nhân gian", "New York"),
        ]

        for sentence, forbidden_city in trick_negatives:
            reply = bot._match_fast_command(sentence)
            if reply is not None:
                self.assertNotIn(
                    forbidden_city,
                    reply,
                    f"Sentence '{sentence}' falsely triggered world clock for '{forbidden_city}'! Reply: {reply}"
                )
                self.assertNotIn(
                    "Bây giờ ở",
                    reply,
                    f"Sentence '{sentence}' falsely triggered world clock marker! Reply: {reply}"
                )

        # True positives: MUST trigger world clock with correct city and time
        trick_positives = [
            # Mission requirements:
            ("giờ ở nước Anh bây giờ là mấy giờ", "London"),
            ("ở Nhật Bản bây giờ mấy giờ", "Tokyo"),
            # Additional genuine world clock queries:
            ("giờ ở London là mấy giờ", "London"),
            ("mấy giờ ở Tokyo", "Tokyo"),
            ("bây giờ ở New York là mấy giờ", "New York"),
            ("giờ hiện tại ở Paris", "Paris"),
            ("ở Berlin bây giờ là mấy giờ", "Berlin"),
            ("giờ ở Sydney", "Sydney"),
            ("bây giờ ở Hàn Quốc là mấy giờ", "Seoul"),
            ("giờ ở Singapore", "Singapore"),
            ("ở Dubai bây giờ mấy giờ", "Dubai"),
            ("mấy giờ ở Los Angeles", "Los Angeles"),
            ("giờ tại Seoul", "Seoul"),
            ("ở Bangkok bây giờ là mấy giờ", "Bangkok"),
        ]

        for sentence, expected_city in trick_positives:
            reply = bot._match_fast_command(sentence)
            self.assertIsNotNone(
                reply,
                f"Sentence '{sentence}' failed to trigger fast world clock!"
            )
            self.assertIn(
                expected_city,
                reply,
                f"Sentence '{sentence}' expected '{expected_city}' but got: {reply}"
            )
            self.assertIn(
                "Bây giờ ở",
                reply,
                f"Sentence '{sentence}' did not include world clock marker 'Bây giờ ở'!"
            )

    # =========================================================================
    # 2. Weather Topic Detection: Mission Requirements & Adversarial Tricks
    # =========================================================================
    def test_02_weather_topic_detection_adversarial(self):
        """Stress-test weather topic detection against tricky homonyms and compound words."""
        # False positives: MUST NOT trigger weather topic
        false_weather = [
            # Mission requirements:
            "SwiftUI có tính năng gì mới?",
            "Hôm nay tôi bị mất ngủ",
            "Tôi muốn mua MacBook",
            # Additional trick sentences:
            "Trời sinh một cặp",
            "Kỹ năng giao tiếp trong công việc",
            "Khả năng mở rộng của microservices",
            "Năng lượng mặt trời là nguồn năng lượng tái tạo",
            "Đi mua sắm quần áo tết ở Aeon Mall",
            "Tôi muốn mua điện thoại iPhone 16",
            "Đi mát-xa chân thư giãn cuối tuần",
            "Mắt biếc là một bộ phim của Victor Vũ",
            "Hôm nay tôi mất chìa khóa xe máy",
            "Tài năng trẻ của bóng đá Việt Nam",
            "Đang làm việc với tinh thần trách nhiệm",
            "Mặt trời mọc ở hướng đông",
            "Hôm nay tôi mua vé xem phim",
        ]

        for sentence in false_weather:
            norm = bot._norm(sentence)
            is_weather = bot._is_weather_topic(sentence, norm)
            self.assertFalse(
                is_weather,
                f"Sentence '{sentence}' falsely classified as WEATHER topic!"
            )

        # True positives: MUST trigger weather topic
        true_weather = [
            # Mission requirements:
            "Hôm nay trời nắng và có mưa rào",
            # Additional true weather sentences:
            "Thời tiết Hà Nội hôm nay thế nào?",
            "Sài Gòn hôm nay có mưa không?",
            "Ngày mai trời có nắng to không em?",
            "Thời tiết Đà Lạt lạnh bao nhiêu độ?",
            "Hôm nay trời oi bức và ẩm ướt quá",
            "Đi chơi ngoài trời mưa phùn cần mang ô",
            "Trời hôm nay âm u nhiều mây",
            "Dự báo thời tiết cuối tuần này",
            "Nhiệt độ hiện tại ở Đà Nẵng",
            "Trời hôm nay mát mẻ dễ chịu thật",
            "Ngoài trời đang mưa to tầm tã",
            "Mặc gì khi đi Đà Lạt hôm nay",
            "Trang phục phù hợp khi trời rét buốt",
        ]

        for sentence in true_weather:
            norm = bot._norm(sentence)
            is_weather = bot._is_weather_topic(sentence, norm)
            self.assertTrue(
                is_weather,
                f"Sentence '{sentence}' was NOT recognized as WEATHER topic!"
            )

    # =========================================================================
    # 3. Code-First Trigger: Mission Requirements & Conceptual Distinctions
    # =========================================================================
    def test_03_code_first_trigger_adversarial(self):
        """Stress-test code-first intent detection against non-code verbs and concept questions."""
        # False positives: MUST NOT trigger Code-First
        non_code_queries = [
            # Mission requirements:
            "Gợi ý mẫu áo khoác",
            "Viết lời chúc sinh nhật",
            "Mình ham học hỏi",
            # Additional trick non-code queries:
            "Viết bài văn cảm nghĩ về mùa thu Hà Nội",
            "Tạo mẫu đơn xin nghỉ phép đi du lịch",
            "Cách viết thư tình lãng mạn cho bạn gái",
            "Giải thích khái niệm biến trong toán học",
            "Mẫu email xin thôi việc chuyên nghiệp",
            "Gợi ý thực đơn ăn kiêng giảm cân",
            "Mình rất ham đọc sách lịch sử",
            # Concept-only queries (should NOT trigger Code-First even with tech keywords):
            "SwiftUI là gì?",
            "Thuật toán là gì?",
            "So sánh Flutter và React Native",
            "Ưu nhược điểm của Python",
            "Làm sao để giỏi lập trình?",
        ]

        for query in non_code_queries:
            is_code = bot._is_code_request(query)
            self.assertFalse(
                is_code,
                f"Query '{query}' falsely triggered Code-First intent!"
            )

        # True positives: MUST trigger Code-First
        code_queries = [
            # Mission requirements:
            "Viết hàm Swift tính Fibonacci",
            # Additional genuine code queries:
            "Viết một hàm Python đọc file csv",
            "Tạo class Flutter quản lý state giỏ hàng",
            "Viết snippet TypeScript xử lý async await",
            "Sửa bug cú pháp trong đoạn code này",
            "Viết thuật toán sắp xếp nổi bọt bằng Python",
            "Cách viết code debounce trong JavaScript",
            "Viết struct Swift đại diện cho UserModel",
            "Gọi API bằng Python requests",
            "Viết code gom nhóm dữ liệu trong SQL",
        ]

        for query in code_queries:
            is_code = bot._is_code_request(query)
            self.assertTrue(
                is_code,
                f"Query '{query}' failed to trigger Code-First intent!"
            )

    # =========================================================================
    # 4. Auto-Learn Profile: Mission Specification Test Cases
    # =========================================================================
    def test_04_auto_learn_profile_mission_spec_cases(self):
        """Verify the 4 specific profile test cases required by Mission M2 dispatch."""
        # Baseline profile
        bot.update_user_profile({
            "name": "Khánh",
            "city": "Sài Gòn",
            "age": "25",
            "weight_kg": "65",
            "height_cm": "170"
        })

        # 1. 'Vali nặng 32kg' -> must NOT update weight
        bot.extract_and_update_profile_from_message("Vali nặng 32kg")
        self.assertEqual(bot.get_user_profile().get("weight_kg"), "65")

        # 2. 'Steve Jobs sinh năm 1955' -> must NOT update age
        bot.extract_and_update_profile_from_message("Steve Jobs sinh năm 1955")
        self.assertEqual(bot.get_user_profile().get("age"), "25")

        # 3. 'Đang ở Đà Lạt du lịch' -> must NOT update permanent city
        bot.extract_and_update_profile_from_message("Đang ở Đà Lạt du lịch")
        self.assertEqual(bot.get_user_profile().get("city"), "Sài Gòn")

        # 4. 'Tên mình là Nguyễn Duy Khánh nha' -> MUST update name to 'Nguyễn Duy Khánh'
        bot.extract_and_update_profile_from_message("Tên mình là Nguyễn Duy Khánh nha")
        self.assertEqual(bot.get_user_profile().get("name"), "Nguyễn Duy Khánh")

    # =========================================================================
    # 5. Adversarial Finding: 3rd-Party Age Overwrite Vulnerability (REMEDIATED)
    # =========================================================================
    def test_05_vulnerability_auto_learn_3rd_person_age_leak(self):
        """
        [ADVERSARIAL VULNERABILITY REPRODUCTION]
        In bot.py line 540, the age regex uses `(?:mình|tôi|anh|em|năm\s+nay(?:\s+mình)?)`.
        Because `(?:\s+mình)?` is optional, any sentence with 'năm nay <X> tuổi' matches,
        corrupting the user's age when discussing 3rd-party subjects (relatives, celebrities).

        Expected behavior: The user's age should remain 25.
        Actual behavior: Fails because bot.py extracts 40 and overwrites user profile age.
        """
        bot.update_user_profile({"age": "25"})
        bot.extract_and_update_profile_from_message("Anh ấy năm nay 40 tuổi rồi")
        # Should NOT overwrite user's age, but currently sets age to "40"
        self.assertEqual(
            bot.get_user_profile().get("age"),
            "25",
            "VULNERABILITY: 3rd-person phrase 'Anh ấy năm nay 40 tuổi' improperly updated user's age to 40!"
        )

    # Alias for backward compatibility with orchestrator / reviewer invocation
    test_auto_learn_profile_adversarial = test_05_vulnerability_auto_learn_3rd_person_age_leak

    # =========================================================================
    # 6. End-to-End get_reply Routing & Weather Context Injection
    # =========================================================================
    def test_06_get_reply_fast_path_routing_and_weather_context(self):
        """Verify full get_reply routing and weather context injection."""
        # 1. World clock pronoun trick must fall through to AI, not return London clock
        with patch.object(bot, "_ask_ai", return_value="Câu trả lời AI") as mock_ai:
            reply = bot.get_reply("Bây giờ anh đói bụng quá")
            self.assertNotIn("London", reply)
            mock_ai.assert_called_once()

        # 2. Genuine world clock must return immediately via fast path
        reply_london = bot.get_reply("giờ ở nước Anh bây giờ là mấy giờ")
        self.assertIn("London", reply_london)
        self.assertIn("Bây giờ ở London là", reply_london)

        reply_tokyo = bot.get_reply("ở Nhật Bản bây giờ mấy giờ")
        self.assertIn("Tokyo", reply_tokyo)
        self.assertIn("Bây giờ ở Tokyo là", reply_tokyo)

        # 3. Weather context injection in _build_system_context
        with patch.object(bot, "get_current_weather", return_value="28°C Nắng đẹp") as mock_weather:
            ctx = bot._build_system_context(
                text_norm="hom nay troi nang va co mua rao",
                raw_message="Hôm nay trời nắng và có mưa rào"
            )
            mock_weather.assert_called_once()
            self.assertIn("[Dữ liệu thời tiết thực tế từ Open-Meteo]", ctx)

        with patch.object(bot, "get_current_weather") as mock_weather_2:
            ctx_tech = bot._build_system_context(
                text_norm="swiftui co tinh nang gi moi",
                raw_message="SwiftUI có tính năng gì mới?"
            )
            mock_weather_2.assert_not_called()
            self.assertNotIn("[Dữ liệu thời tiết thực tế từ Open-Meteo]", ctx_tech)

    # =========================================================================
    # 7. Adversarial Finding Iteration 2: Possessive & Compound Relatives Leaks
    # =========================================================================
    def test_07_vulnerability_possessive_and_compound_relatives_leak(self):
        """
        [ADVERSARIAL VULNERABILITY REPRODUCTION - ITERATION 2]
        In bot.py, _is_preceded_by_possessive and the regex lookbehind emulation only checks
        a hardcoded 14-word list immediately adjacent to the pronoun (e.g. 'Bạn mình').
        It fails to block:
        1. Possessives with 'của' ('Bố của mình năm nay 60 tuổi', 'Bạn của mình nặng 80kg', 'Vợ của mình cao 1m60')
        2. Compound family words ending in 'gái'/'trai' ('Con gái mình năm nay 10 tuổi', 'Anh trai mình năm nay 30 tuổi')
        3. Common extended relatives ('Bác mình năm nay 70 tuổi', 'Ông mình năm nay 80 tuổi')
        4. Possessions / pets ('Chó của mình nặng 35kg', 'Xe của mình nặng 120kg')

        Expected: Profile must NOT be overwritten when discussing 3rd parties or possessions.
        """
        bot.update_user_profile({
            "name": "Khánh",
            "age": "25",
            "weight_kg": "65",
            "height_cm": "170"
        })

        # Test possessive 'của'
        bot.extract_and_update_profile_from_message("Bố của mình năm nay 60 tuổi")
        self.assertEqual(
            bot.get_user_profile().get("age"),
            "25",
            "VULNERABILITY: 'Bố của mình năm nay 60 tuổi' overwrote age to 60!"
        )

        bot.extract_and_update_profile_from_message("Vợ của mình cao 1m60")
        self.assertEqual(
            bot.get_user_profile().get("height_cm"),
            "170",
            "VULNERABILITY: 'Vợ của mình cao 1m60' overwrote height to 160!"
        )

        bot.extract_and_update_profile_from_message("Con gái mình năm nay 10 tuổi")
        self.assertEqual(
            bot.get_user_profile().get("age"),
            "25",
            "VULNERABILITY: 'Con gái mình năm nay 10 tuổi' overwrote age to 10!"
        )

    # =========================================================================
    # 8. Adversarial Extreme Kinship, 3rd-Party Entities, and Profile Hardening
    # =========================================================================
    def test_08_adversarial_extreme_kinship_and_3rd_party_stress(self):
        """
        Adversarial Extreme Stress Test (Iteration 3):
        Verify that 3rd-party kinship, friends, pets, and property NEVER overwrite user_profile.json:
        - 'Bố của mình 60 tuổi'
        - 'Con gái mình 10 tuổi'
        - 'Bạn thân của mình nặng 80kg'
        - 'Anh họ mình sống ở Huế'
        - 'Xe của mình nặng 120kg'
        - 'Chó của mình 3 tuổi'
        And verify genuine 1st-person statements are accurately extracted.
        """
        baseline = {
            "name": "Duy Khánh",
            "gender": "Nam",
            "city": "Hồ Chí Minh",
            "age": "26",
            "weight_kg": "68",
            "height_cm": "172"
        }

        # 1. 3rd-party rejection stress suite
        adversarial_3rd_party_cases = [
            # Orchestrator explicit targets:
            ("Bố của mình 60 tuổi", "age", "26"),
            ("Con gái mình 10 tuổi", "age", "26"),
            ("Bạn thân của mình nặng 80kg", "weight_kg", "68"),
            ("Anh họ mình sống ở Huế", "city", "Hồ Chí Minh"),
            ("Xe của mình nặng 120kg", "weight_kg", "68"),
            ("Chó của mình 3 tuổi", "age", "26"),
            # Extended stress cases:
            ("Mẹ của mình 55 tuổi", "age", "26"),
            ("Bà ngoại của mình 80 tuổi", "age", "26"),
            ("Ông nội mình sinh năm 1940", "age", "26"),
            ("Chị dâu mình sống ở Đà Nẵng", "city", "Hồ Chí Minh"),
            ("Em gái mình ở Cần Thơ", "city", "Hồ Chí Minh"),
            ("Chó của mình nặng 35kg", "weight_kg", "68"),
            ("Mèo của mình nặng 5kg", "weight_kg", "68"),
            ("Vali của mình nặng 25kg", "weight_kg", "68"),
            ("Hành lý của mình nặng 30kg", "weight_kg", "68"),
            ("Kiện hàng của mình nặng 50kg", "weight_kg", "68"),
            ("Cây mai của mình cao 1m80", "height_cm", "172"),
            ("Vợ của mình cao 1m60", "height_cm", "172"),
            ("Chồng của mình cao 180cm", "height_cm", "172"),
            ("Con gái mình cao 1m30", "height_cm", "172"),
            ("Người yêu mình cao 1m65", "height_cm", "172"),
            ("Con gái mình tên là Lan", "name", "Duy Khánh"),
            ("Bạn của mình tên là Tuấn", "name", "Duy Khánh"),
            ("Bồ cũ của mình tên là Mai", "name", "Duy Khánh"),
            ("Crush của mình tên là Linh", "name", "Duy Khánh"),
        ]

        for sentence, field, expected_val in adversarial_3rd_party_cases:
            bot.update_user_profile(baseline)
            bot.extract_and_update_profile_from_message(sentence)
            actual_val = bot.get_user_profile().get(field)
            self.assertEqual(
                actual_val,
                expected_val,
                f"3rd-party statement '{sentence}' leaked into user profile field '{field}'! Value: '{actual_val}'"
            )

        # 2. Genuine 1st-person statements must be extracted accurately (100%)
        # Age
        bot.update_user_profile({"age": "20"})
        bot.extract_and_update_profile_from_message("Chào bạn mình 26 tuổi")
        self.assertEqual(bot.get_user_profile().get("age"), "26")

        bot.update_user_profile({"age": "20"})
        bot.extract_and_update_profile_from_message("Tôi 30 tuổi")
        self.assertEqual(bot.get_user_profile().get("age"), "30")

        bot.update_user_profile({"age": "20"})
        bot.extract_and_update_profile_from_message("Tuổi của anh năm nay 28 tuổi")
        self.assertEqual(bot.get_user_profile().get("age"), "28")

        # Height
        bot.update_user_profile({"height_cm": "160"})
        bot.extract_and_update_profile_from_message("Chiều cao của mình là 1m75")
        self.assertEqual(bot.get_user_profile().get("height_cm"), "175")

        bot.update_user_profile({"height_cm": "160"})
        bot.extract_and_update_profile_from_message("Chiều cao của anh là 1m78")
        self.assertEqual(bot.get_user_profile().get("height_cm"), "178")

        # Weight
        bot.update_user_profile({"weight_kg": "60"})
        bot.extract_and_update_profile_from_message("Mình nặng 70kg")
        self.assertEqual(bot.get_user_profile().get("weight_kg"), "70")

        bot.update_user_profile({"weight_kg": "60"})
        bot.extract_and_update_profile_from_message("Cân nặng của mình là 70kg")
        self.assertEqual(bot.get_user_profile().get("weight_kg"), "70")

        bot.update_user_profile({"weight_kg": "60"})
        bot.extract_and_update_profile_from_message("Cân nặng của em là 52kg")
        self.assertEqual(bot.get_user_profile().get("weight_kg"), "52")


if __name__ == "__main__":
    unittest.main()

