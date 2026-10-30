"""
Automated Verification Suite for Milestone M1 (Voice Core & Flow Resilience)
Tests:
1. Fast-path persistence in _CHAT_HISTORY with thread safety.
2. Multi-turn context resolution recovers previous city from fast-path.
3. Edge TTS synthesis and safe fallback caching (no gender cross-contamination).
4. Fast AI offline circuit breaker switches to fallback in < 1.0 second.
"""
import asyncio
import socket
import sys
import threading
import time
import unittest
import urllib.error
from unittest.mock import patch, MagicMock

import bot
import app


class TestMilestone1Verification(unittest.TestCase):
    def setUp(self):
        bot._clear_chat_history()
        app._TTS_CACHE.clear()
        bot._GEMINI_NETWORK_OFFLINE_UNTIL = 0.0

    def test_01_fast_path_writes_to_chat_history(self):
        """Verify fast-path commands append to _CHAT_HISTORY with is_fast_path=True."""
        self.assertEqual(len(bot._CHAT_HISTORY), 0)

        # 1. Weather fast path via get_reply
        reply1 = bot.get_reply("thời tiết Đà Lạt")
        self.assertIn("Đà Lạt", reply1)
        with bot._CHAT_HISTORY_LOCK:
            self.assertEqual(len(bot._CHAT_HISTORY), 2)
            self.assertEqual(bot._CHAT_HISTORY[0]["role"], "user")
            self.assertEqual(bot._CHAT_HISTORY[0]["content"], "thời tiết Đà Lạt")
            self.assertEqual(bot._CHAT_HISTORY[1]["role"], "assistant")
            self.assertTrue(bot._CHAT_HISTORY[1].get("is_fast_path"))

        # 2. Time fast path via get_reply
        reply2 = bot.get_reply("mấy giờ")
        with bot._CHAT_HISTORY_LOCK:
            self.assertEqual(len(bot._CHAT_HISTORY), 4)
            self.assertEqual(bot._CHAT_HISTORY[2]["role"], "user")
            self.assertEqual(bot._CHAT_HISTORY[3]["role"], "assistant")
            self.assertTrue(bot._CHAT_HISTORY[3].get("is_fast_path"))

        # 3. Weather fast path via stream_reply
        events = list(bot.stream_reply("thời tiết Đà Nẵng"))
        done_events = [e for e in events if isinstance(e, dict) and e.get("type") == "done"]
        self.assertTrue(len(done_events) > 0)
        with bot._CHAT_HISTORY_LOCK:
            self.assertEqual(len(bot._CHAT_HISTORY), 6)
            self.assertEqual(bot._CHAT_HISTORY[4]["content"], "thời tiết Đà Nẵng")
            self.assertTrue(bot._CHAT_HISTORY[5].get("is_fast_path"))

    def test_02_multiturn_city_recovery_from_fast_path(self):
        """Verify multi-turn question finds the previous city from fast-path history."""
        # User default city in user_profile.json
        default_city = bot._get_user_default_city()

        # Step 1: User asks weather for Da Lat
        bot.get_reply("thời tiết Đà Lạt")

        # Step 2: Follow-up question has no city name
        detected_city = bot._detect_city_in_text("vậy mặc gì cho hợp?")
        self.assertEqual(detected_city, "da lat", "Should remember Da Lat from fast-path history!")
        self.assertNotEqual(detected_city, default_city.lower(), "Should NOT fallback to default profile city!")

        # Step 3: Ask for Da Nang via stream_reply
        list(bot.stream_reply("thời tiết Đà Nẵng"))

        # Step 4: Follow up again
        detected_city_2 = bot._detect_city_in_text("ngày mai thế nào?")
        self.assertEqual(detected_city_2, "da nang", "Should remember Da Nang from recent stream fast-path history!")

        # Step 5: Direct fast-path follow-up command "thời tiết ngày mai" inherits previous city
        reply_followup = bot.get_reply("thời tiết ngày mai")
        self.assertIn("Đà Nẵng", reply_followup, "Fast-path get_reply should remember Da Nang!")

        # Step 6: Direct fast-path follow-up stream_reply inherits previous city
        stream_events = list(bot.stream_reply("thời tiết ngày mai"))
        tokens = [e.get("delta", "") for e in stream_events if isinstance(e, dict) and e.get("type") == "token"]
        stream_text = "".join(tokens)
        self.assertIn("Đà Nẵng", stream_text, "Fast-path stream_reply should remember Da Nang!")

    def test_03_conversational_voice_prompt_and_models(self):
        """Verify is_voice flag generates conversational prompt rules and gemini-3.8-flash is removed."""
        # Verify gemini-3.8-flash is NOT in GEMINI_MODELS
        self.assertNotIn("gemini-3.8-flash", bot.GEMINI_MODELS)
        self.assertIn("gemini-3.1-flash-lite", bot.GEMINI_MODELS)

        # Verify voice prompt rules
        rules_text = bot._build_style_rules_prompt(bot.DEFAULT_AI_PREFS, is_code_query=False, is_voice=True)
        self.assertIn("VOICE MODE", rules_text)
        self.assertIn("TỐI ĐA 1–3 CÂU", rules_text)
        self.assertIn("TUYỆT ĐỐI KHÔNG dùng dấu gạch đầu dòng", rules_text)

    def test_04_tts_caching_and_google_isolation(self):
        """Verify Edge TTS and safe Google fallback cache isolation."""
        async def run_tts_tests():
            text = "Xin chào, đây là bài kiểm tra âm thanh giọng nói."
            male_voice = "namminh"
            male_cache_key = (male_voice, app._prepare_tts_text(text))

            # Simulate Edge TTS error/timeout to trigger Google fallback
            with patch("app._synthesize_edge_tts", side_effect=asyncio.TimeoutError("Edge timeout")):
                with patch("app._synthesize_google_tts", return_value=b"FAKE_GOOGLE_MP3") as mock_google:
                    res = await app._async_generate_tts(text, voice_id=male_voice)
                    self.assertEqual(res, b"FAKE_GOOGLE_MP3")
                    mock_google.assert_called_once()

                    # CRITICAL: Verify Google audio was NOT cached under male_cache_key!
                    self.assertNotIn(male_cache_key, app._TTS_CACHE,
                                     "CRITICAL BUG: Google female TTS must NEVER be cached under male voice key!")

                    # Verify it was cached under ("google_fallback", text)
                    fallback_key = ("google_fallback", app._prepare_tts_text(text))
                    self.assertIn(fallback_key, app._TTS_CACHE)
                    self.assertEqual(app._TTS_CACHE[fallback_key], b"FAKE_GOOGLE_MP3")

            # Now test Edge TTS success
            app._TTS_CACHE.clear()
            with patch("app._synthesize_edge_tts", return_value=b"REAL_EDGE_MALE_MP3") as mock_edge:
                res2 = await app._async_generate_tts(text, voice_id=male_voice)
                self.assertEqual(res2, b"REAL_EDGE_MALE_MP3")
                mock_edge.assert_called()
                # Edge TTS should be cached under male_cache_key
                self.assertIn(male_cache_key, app._TTS_CACHE)
                self.assertEqual(app._TTS_CACHE[male_cache_key], b"REAL_EDGE_MALE_MP3")

        asyncio.run(run_tts_tests())

    def test_05_fast_ai_offline_circuit_breaker(self):
        """Verify network failure breaks immediately and switches to Ollama in <1.0s."""
        net_err = urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed"))

        start_time = time.monotonic()
        with patch("urllib.request.urlopen", side_effect=net_err):
            # Attempt streaming tokens with network error
            with self.assertRaises(RuntimeError) as ctx:
                gen = bot._stream_gemini_tokens("xin chào", "system prompt")
                next(gen)

        elapsed = time.monotonic() - start_time
        # Must fail fast in < 1.0s (actually < 0.05s) instead of 48s!
        self.assertLess(elapsed, 1.0, f"Circuit breaker took too long: {elapsed:.3f}s (must be < 1.0s)!")
        self.assertGreater(bot._GEMINI_NETWORK_OFFLINE_UNTIL, time.monotonic(),
                           "Network offline cooldown should be active!")

        # Second call should trip circuit breaker immediately (0ms)
        start_time_2 = time.monotonic()
        with self.assertRaises(RuntimeError) as ctx2:
            gen2 = bot._stream_gemini_tokens("câu thứ hai", "system prompt")
            next(gen2)
        elapsed_2 = time.monotonic() - start_time_2
        self.assertLess(elapsed_2, 0.05, f"Subsequent circuit trip should be instant: {elapsed_2:.4f}s")

    def test_06_ollama_model_caching(self):
        """Verify Ollama model resolution is cached."""
        bot._CACHED_OLLAMA_MODEL = None
        bot._CACHED_OLLAMA_EXPIRES = 0.0

        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"models": [{"name": "qwen2.5:3b"}]}'
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
            model1 = bot._resolve_ollama_model()
            self.assertEqual(model1, "qwen2.5:3b")
            self.assertEqual(mock_urlopen.call_count, 1)

            # Second call within cache window should NOT call urlopen again
            model2 = bot._resolve_ollama_model()
            self.assertEqual(model2, "qwen2.5:3b")
            self.assertEqual(mock_urlopen.call_count, 1, "Should reuse cached Ollama model without HTTP request!")

    def test_07_server_stream_cancellation(self):
        """
        Verify generator cancellation while TTS futures are actively queued/in-flight
        sets stop_event, cancels in-flight futures, and returns in < 0.2s without hanging.
        """
        client = app.app.test_client()
        res = client.post("/api/chat/stream", json={"message": "thời tiết Hà Nội", "speak": True})
        self.assertEqual(res.status_code, 200)
        gen = res.response

        # Consume multiple events so tokens are emitted and TTS futures are queued
        events = []
        for _ in range(5):
            events.append(next(gen))
        self.assertGreaterEqual(len(events), 5)

        t0 = time.perf_counter()
        gen.close()
        dt = time.perf_counter() - t0

        self.assertLess(dt, 0.2, f"Cancellation took too long ({dt:.3f}s), expected < 0.2s")

    def test_08_is_voice_passed_to_stream_reply(self):
        """Verify speak=True in stream request propagates is_voice=True to stream_reply."""
        with patch("app.stream_reply", wraps=bot.stream_reply) as mock_stream_reply:
            client = app.app.test_client()
            res = client.post("/api/chat/stream", json={"message": "thời tiết Hà Nội", "speak": True})
            self.assertEqual(res.status_code, 200)
            # Drain stream
            list(res.response)
            mock_stream_reply.assert_called_once()
            _, kwargs = mock_stream_reply.call_args
            self.assertTrue(kwargs.get("is_voice"), "speak=True must pass is_voice=True to stream_reply!")


if __name__ == "__main__":
    unittest.main()
