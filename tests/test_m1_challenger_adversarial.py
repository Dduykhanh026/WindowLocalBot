"""
Challenger Adversarial Test Suite for Milestone M1 (Voice Core & Flow Resilience)
Author: Empirical Challenger 2
Tests:
1. TTS Cache Isolation Stress Test under extreme failures (timeouts, network errors, empty bytes).
2. TTS Cache Recovery and non-contamination across all voice profiles.
3. High-concurrency TTS stress test (50 concurrent async requests).
4. Multi-turn context retention across 6+ turns (interleaved weather, time, advisory queries).
5. Adversarial edge case: fast-path weather follow-up context retention.
"""
import asyncio
import os
import random
import re
import sys
import threading
import time
import unittest
from unittest.mock import patch, MagicMock

import app
import bot


class TestM1ChallengerAdversarial(unittest.TestCase):
    def setUp(self):
        bot._clear_chat_history()
        app._TTS_CACHE.clear()
        bot._GEMINI_NETWORK_OFFLINE_UNTIL = 0.0

    # =========================================================================
    # PART 1: TTS CACHE ISOLATION STRESS TESTS
    # =========================================================================

    def test_tts_extreme_edge_failures_isolation(self):
        """
        Stress test TTS Cache Isolation under multiple extreme failure modes of Edge TTS:
        - asyncio.TimeoutError
        - ConnectionResetError
        - OSError (Network down)
        - Empty bytes returned
        - Arbitrary Exception
        Verify that Google fallback is strictly stored under ("google_fallback", clean_text)
        and NEVER poisons ("namminh", ...) or ("namminh_deep", ...).
        """
        failure_modes = [
            asyncio.TimeoutError("Edge TTS timed out after 2.2s"),
            ConnectionResetError("Edge WebSocket connection reset by peer"),
            OSError("Network is unreachable"),
            Exception("Edge Neural TTS internal synthesis failure"),
        ]

        async def run_stress():
            for i, failure_err in enumerate(failure_modes):
                text = f"Kiểm tra cách ly bộ nhớ đệm trường hợp lỗi số {i + 1}"
                clean = app._prepare_tts_text(text)
                google_audio = f"MOCK_GOOGLE_AUDIO_PAYLOAD_{i}".encode("utf-8")

                for male_voice in ["namminh", "namminh_deep"]:
                    app._TTS_CACHE.clear()
                    male_key = (male_voice, clean)
                    fallback_key = ("google_fallback", clean)

                    with patch("app._synthesize_edge_tts", side_effect=failure_err):
                        with patch("app._synthesize_google_tts", return_value=google_audio) as mock_google:
                            res = await app._async_generate_tts(text, voice_id=male_voice)

                            self.assertEqual(res, google_audio, f"Should return Google fallback on {failure_err}")
                            self.assertEqual(mock_google.call_count, 1)

                            # CRITICAL CHECK: male key must NOT exist in cache
                            self.assertNotIn(
                                male_key,
                                app._TTS_CACHE,
                                f"POISON DETECTED: Male key {male_key} was polluted by Google audio!",
                            )

                            # Fallback key must store the Google audio
                            self.assertIn(fallback_key, app._TTS_CACHE)
                            self.assertEqual(app._TTS_CACHE[fallback_key], google_audio)

                            # Verify NO male key exists in cache at all
                            for k, v in app._TTS_CACHE.items():
                                self.assertNotEqual(k[0], "namminh", f"Key {k} is male voice holding Google audio!")
                                self.assertNotEqual(k[0], "namminh_deep", f"Key {k} is male voice holding Google audio!")

            # Also test when Edge TTS returns empty bytes b""
            text_empty = "Kiểm tra Edge trả về chuỗi rỗng"
            clean_empty = app._prepare_tts_text(text_empty)
            google_audio_empty = b"GOOGLE_AUDIO_FOR_EMPTY_EDGE"
            male_key_empty = ("namminh", clean_empty)
            fallback_key_empty = ("google_fallback", clean_empty)

            app._TTS_CACHE.clear()
            with patch("app._synthesize_edge_tts", return_value=b""):
                with patch("app._synthesize_google_tts", return_value=google_audio_empty):
                    res = await app._async_generate_tts(text_empty, voice_id="namminh")
                    self.assertEqual(res, google_audio_empty)
                    self.assertNotIn(male_key_empty, app._TTS_CACHE)
                    self.assertIn(fallback_key_empty, app._TTS_CACHE)

        asyncio.run(run_stress())

    def test_tts_recovery_after_edge_outage(self):
        """
        Verify that once Edge TTS recovers:
        1. Subsequent request with male voice invokes Edge TTS and does NOT return the Google audio.
        2. Male audio is stored under ("namminh", clean_text).
        3. ("google_fallback", clean_text) remains independent and intact.
        """
        async def run_recovery():
            text = "Kiểm tra khôi phục sau sự cố mạng Edge TTS."
            clean = app._prepare_tts_text(text)
            male_key = ("namminh", clean)
            fallback_key = ("google_fallback", clean)
            mock_google_bytes = b"FALLBACK_GOOGLE_MP3"
            mock_edge_bytes = b"AUTHENTIC_EDGE_NAMMINH_MALE_MP3"

            # Phase 1: Edge TTS down -> Google fallback used
            with patch("app._synthesize_edge_tts", side_effect=asyncio.TimeoutError("Timeout")):
                with patch("app._synthesize_google_tts", return_value=mock_google_bytes):
                    res1 = await app._async_generate_tts(text, voice_id="namminh")
                    self.assertEqual(res1, mock_google_bytes)
                    self.assertNotIn(male_key, app._TTS_CACHE)
                    self.assertIn(fallback_key, app._TTS_CACHE)

            # Phase 2: Edge TTS recovers
            with patch("app._synthesize_edge_tts", return_value=mock_edge_bytes) as mock_edge:
                with patch("app._synthesize_google_tts", return_value=mock_google_bytes) as mock_google:
                    res2 = await app._async_generate_tts(text, voice_id="namminh")
                    # Must call Edge TTS, NOT reuse Google fallback
                    self.assertEqual(res2, mock_edge_bytes)
                    mock_edge.assert_called_once()
                    mock_google.assert_not_called()

                    # Male key is now cached with authentic Edge audio
                    self.assertIn(male_key, app._TTS_CACHE)
                    self.assertEqual(app._TTS_CACHE[male_key], mock_edge_bytes)

                    # Fallback key still preserved with Google audio
                    self.assertIn(fallback_key, app._TTS_CACHE)
                    self.assertEqual(app._TTS_CACHE[fallback_key], mock_google_bytes)

        asyncio.run(run_recovery())

    def test_tts_concurrent_isolation_stress(self):
        """
        Stress test 50 concurrent async TTS calls across different voices and texts.
        Randomly simulate Edge TTS successes and failures.
        Verify that across all 50 executions, male keys NEVER receive Google audio.
        """
        GOOGLE_SIG = b"SIGNATURE_GOOGLE_FEMALE"
        EDGE_SIG = b"SIGNATURE_EDGE_AUDIO"

        async def worker(idx: int):
            voice = random.choice(["namminh", "namminh_deep", "hoaimy", "ava_multilingual"])
            text = f"Câu kiểm tra đồng thời đa tác vụ số {idx % 7}"
            fail_edge = (idx % 2 == 0)

            async def mock_edge(*args, **kwargs):
                if fail_edge:
                    await asyncio.sleep(0.01)
                    raise asyncio.TimeoutError("Simulated timeout")
                await asyncio.sleep(0.01)
                return EDGE_SIG + f"_{voice}".encode()

            def mock_google(t):
                return GOOGLE_SIG

            with patch("app._synthesize_edge_tts", side_effect=mock_edge):
                with patch("app._synthesize_google_tts", side_effect=mock_google):
                    return await app._async_generate_tts(text, voice_id=voice)

        async def run_all():
            tasks = [worker(i) for i in range(50)]
            results = await asyncio.gather(*tasks)
            self.assertEqual(len(results), 50)

            # Verify cache integrity
            for (vid, cl_text), cached_bytes in app._TTS_CACHE.items():
                if vid in ("namminh", "namminh_deep"):
                    self.assertFalse(
                        cached_bytes.startswith(GOOGLE_SIG),
                        f"CRITICAL CONTAMINATION: Male key ({vid}, {cl_text}) contains Google audio!",
                    )
                if vid == "google_fallback":
                    self.assertTrue(cached_bytes.startswith(GOOGLE_SIG))

        asyncio.run(run_all())

    # =========================================================================
    # PART 2: MULTI-TURN FOLLOW-UP CONTEXT CHAIN TESTS (5+ TURNS)
    # =========================================================================

    def test_multiturn_interleaved_context_chain(self):
        """
        Execute an empirical 6-turn conversation script with interleaved:
        Turn 1: Weather query for Da Lat (fast-path)
        Turn 2: Advisory follow-up without city name: "mặc gì cho hợp?" (AI path)
        Turn 3: System clock query: "mấy giờ rồi" (fast-path)
        Turn 4: Follow-up contextual forecast query: "ngày mai thế nào?" (AI path)
        Turn 5: Explicit topic switch: "thời tiết Sài Gòn" (fast-path)
        Turn 6: Follow-up advisory query: "ngoài đường có mưa không?" (AI path)

        Verify:
        - Turns 1-4 retain context of Da Lat.
        - Turn 5 switches context cleanly to Sai Gon.
        - Turn 6 accurately resolves Sai Gon (not Da Lat, not default Hue).
        - Chat history contains all 6 turns and persists properly.
        """
        # --- PHASE A: get_reply() ---
        bot._clear_chat_history()
        default_city = bot._get_user_default_city()

        # Turn 1
        r1 = bot.get_reply("thời tiết Đà Lạt")
        self.assertIn("Đà Lạt", r1)

        # Turn 2: Contextual clothing advice
        r2 = bot.get_reply("mặc gì cho hợp?")
        r2_norm = bot._norm(r2)
        # Should refer to Da Lat weather or cool/cold temperature in Da Lat
        detected_city_t2 = bot._detect_city_in_text("mặc gì cho hợp?")
        self.assertEqual(detected_city_t2, "da lat")
        self.assertTrue(
            any(w in r2_norm for w in ("da lat", "lanh", "am", "khoac", "mat")),
            f"Turn 2 should be relevant to Da Lat weather: {r2}",
        )

        # Turn 3: Interleaved clock
        r3 = bot.get_reply("mấy giờ rồi")
        self.assertIn("Bây giờ là", r3)

        # Turn 4: Follow-up forecast
        r4 = bot.get_reply("ngày mai thế nào?")
        r4_norm = bot._norm(r4)
        detected_city_t4 = bot._detect_city_in_text("ngày mai thế nào?")
        self.assertEqual(detected_city_t4, "da lat")
        if "dang khoi dong bo nao ai local" not in r4_norm and r4_norm:
            self.assertTrue(
                "da lat" in r4_norm or "dalat" in r4_norm,
                f"Turn 4 should retain Da Lat context: {r4}",
            )
        self.assertNotIn("huế", r4_norm, "Turn 4 must NOT fall back to default profile city Huế!")

        # Turn 5: Switch city to Sai Gon
        r5 = bot.get_reply("thời tiết Sài Gòn")
        self.assertTrue("Hồ Chí Minh" in r5 or "Sài Gòn" in r5)

        # Turn 6: Rain follow-up
        r6 = bot.get_reply("ngoài đường có mưa không?")
        r6_norm = bot._norm(r6)
        detected_city_t6 = bot._detect_city_in_text("ngoài đường có mưa không?")
        self.assertEqual(detected_city_t6, "sai gon")
        self.assertNotEqual(detected_city_t6, "da lat")
        if "dang khoi dong bo nao ai local" not in r6_norm and r6_norm:
            self.assertTrue(
                any(w in r6_norm for w in ("sai gon", "ho chi minh", "mua", "duong")),
                f"Turn 6 should address Sai Gon rain: {r6}",
            )

        # Verify chat history length
        with bot._CHAT_HISTORY_LOCK:
            if "dang khoi dong bo nao ai local" in r4_norm or "dang khoi dong bo nao ai local" in r6_norm:
                self.assertGreaterEqual(len(bot._CHAT_HISTORY), 8)
            else:
                self.assertEqual(len(bot._CHAT_HISTORY), 12, "6 turns should produce 12 history entries (user + assistant)")

    def test_multiturn_stream_reply_voice_mode_context_chain(self):
        """
        Execute the exact same 6-turn sequence via stream_reply(is_voice=True).
        Verify that streaming yields concise, conversational responses and retains
        the correct city context at each step.
        """
        bot._clear_chat_history()

        turns = [
            ("thời tiết Đà Lạt", "da lat"),
            ("mặc gì cho hợp?", "da lat"),
            ("mấy giờ rồi", None),
            ("ngày mai thế nào?", "da lat"),
            ("thời tiết Sài Gòn", "sai gon"),
            ("ngoài đường có mưa không?", "sai gon"),
        ]

        replies = []
        for query, expected_city in turns:
            events = list(bot.stream_reply(query, is_voice=True))
            done_evts = [e for e in events if isinstance(e, dict) and e.get("type") == "done"]
            self.assertTrue(len(done_evts) > 0, f"Query '{query}' did not emit done event")
            tokens = [e.get("delta", "") for e in events if isinstance(e, dict) and e.get("type") == "token"]
            rep = done_evts[0].get("reply", "") or "".join(tokens)
            replies.append(rep)

            if expected_city:
                det = bot._detect_city_in_text(query)
                self.assertEqual(det, expected_city, f"Query '{query}' detected '{det}', expected '{expected_city}'")

        # Turn 1: Da Lat weather
        self.assertIn("Đà Lạt", replies[0])

        # Turn 2: Da Lat clothing
        rep2_norm = bot._norm(replies[1])
        if "dang khoi dong bo nao ai local" not in rep2_norm and rep2_norm:
            self.assertTrue(
                any(w in rep2_norm for w in ("da lat", "khoac", "lanh", "mat", "am")),
                f"Stream Turn 2 should mention Da Lat clothing: {replies[1]}",
            )

        # Turn 3: Time
        self.assertIn("Bây giờ là", replies[2])

        # Turn 4: Da Lat tomorrow
        rep4_norm = bot._norm(replies[3])
        if "dang khoi dong bo nao ai local" not in rep4_norm and rep4_norm:
            self.assertTrue(
                "da lat" in rep4_norm or "dalat" in rep4_norm,
                f"Stream Turn 4 should retain Da Lat: {replies[3]}",
            )

        # Turn 5: Sai Gon weather
        self.assertTrue("Hồ Chí Minh" in replies[4] or "Sài Gòn" in replies[4])

        # Turn 6: Sai Gon rain
        rep6_norm = bot._norm(replies[5])
        if "dang khoi dong bo nao ai local" not in rep6_norm and rep6_norm:
            self.assertTrue(
                any(w in rep6_norm for w in ("sai gon", "ho chi minh", "mua")),
                f"Stream Turn 6 should retain Sai Gon context: {replies[5]}",
            )

    # =========================================================================
    # PART 3: ADVERSARIAL EDGE CASE: FAST-PATH FOLLOW-UP WITH OMITTED CITY
    # =========================================================================

    def test_adversarial_fast_path_omitted_city_behavior(self):
        """
        Adversarial Test on Fast-path Weather Queries:
        When a user asks:
        Turn 1: 'thời tiết Đà Lạt'
        Turn 2: 'thời tiết ngày mai' (contains keyword 'thời tiết', but omits city)

        Under current implementation:
        - _detect_city_in_text('thời tiết ngày mai') successfully resolves 'da lat' from history.
        - However, in bot.get_reply() and bot.stream_reply(), fast-path branch calls:
            city = _extract_city_after(n, ...) or _get_user_default_city()
          instead of calling _detect_city_in_text(n).
        This causes the response to abruptly fall back to the default profile city ('Huế')!
        This test documents this empirical finding.
        """
        bot._clear_chat_history()
        bot.get_reply("thời tiết Đà Lạt")

        # Check what helper function returns
        norm_t2 = bot._norm("thời tiết ngày mai")
        detected = bot._detect_city_in_text(norm_t2)
        self.assertEqual(detected, "da lat", "_detect_city_in_text correctly finds 'da lat' from history")

        # Now observe what get_reply actually produces
        reply_t2 = bot.get_reply("thời tiết ngày mai")

        # Document whether it fell back to Hue (the identified flaw) or retained Da Lat
        default_city_name = bot.get_user_profile().get("city", "Huế")
        has_da_lat = "Đà Lạt" in reply_t2
        has_default_city = default_city_name.lower() in reply_t2.lower()

        # If it has default city instead of Da Lat, we record this as an empirical finding
        if has_default_city and not has_da_lat:
            print("\n[EMPIRICAL ADVERSARIAL FINDING]: Fast-path command 'thời tiết ngày mai' falls back to default profile city because get_reply/stream_reply calls _extract_city_after() instead of _detect_city_in_text().")


if __name__ == "__main__":
    unittest.main()
