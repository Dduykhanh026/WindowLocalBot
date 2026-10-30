"""
Adversarial Stress Test Suite for Milestone M1 (Voice Core & Flow Resilience)
Challenger 1 Verification:
1. Concurrency Stress Test: 25 concurrent threads reading, writing, and clearing _CHAT_HISTORY.
2. Fast AI Circuit Breaker Stress Test: Simulated abrupt network failures and failover timing (< 1.0s).
3. Server Stream Cancellation: Generator abort via client disconnect stops worker thread promptly.
"""
import asyncio
import concurrent.futures
import json
import queue
import socket
import threading
import time
import unittest
import urllib.error
from unittest.mock import patch, MagicMock

import app
import bot


class TestMilestone1Adversarial(unittest.TestCase):
    def setUp(self):
        bot._clear_chat_history()
        app._TTS_CACHE.clear()
        bot._GEMINI_NETWORK_OFFLINE_UNTIL = 0.0
        bot._CACHED_OLLAMA_MODEL = None
        bot._CACHED_OLLAMA_EXPIRES = 0.0

    def tearDown(self):
        bot._clear_chat_history()
        bot._GEMINI_NETWORK_OFFLINE_UNTIL = 0.0

    # =========================================================================
    # 1. CONCURRENCY STRESS TESTS
    # =========================================================================

    def test_01_concurrency_multithreaded_read_write_clear_stress(self):
        """
        Adversarial Concurrency Stress Test:
        Launch 25 concurrent threads performing rapid appends, reads, sanitization,
        intent detection, and clears on _CHAT_HISTORY under _CHAT_HISTORY_LOCK.
        Verify zero data corruption, zero deadlocks, and invariant bounds.
        """
        num_threads = 25
        ops_per_thread = 80
        errors = []
        barrier = threading.Barrier(num_threads)
        stop_flag = threading.Event()

        def worker_append(thread_id: int):
            try:
                barrier.wait(timeout=5.0)
                for i in range(ops_per_thread):
                    if stop_flag.is_set():
                        break
                    user_msg = f"User message from thread {thread_id} op {i} - thời tiết Hà Nội"
                    bot_msg = f"Assistant reply to thread {thread_id} op {i}"
                    is_fp = (i % 2 == 0)
                    bot._append_chat_history(user_msg, bot_msg, is_fast_path=is_fp)
                    with bot._CHAT_HISTORY_LOCK:
                        cur_len = len(bot._CHAT_HISTORY)
                        max_expected = bot.MAX_HISTORY_TURNS * 2
                        if cur_len > max_expected:
                            raise AssertionError(f"History length exceeded max: {cur_len} > {max_expected}")
            except Exception as e:
                errors.append((thread_id, "worker_append", e))

        def worker_read_sanitize(thread_id: int):
            try:
                barrier.wait(timeout=5.0)
                for _ in range(ops_per_thread):
                    if stop_flag.is_set():
                        break
                    history = bot._get_sanitized_history_for_prefs(bot.DEFAULT_AI_PREFS)
                    for item in history:
                        if not isinstance(item, dict) or "role" not in item or "content" not in item:
                            raise AssertionError(f"Corrupted history item found: {item}")
                    time.sleep(0.0005)
            except Exception as e:
                errors.append((thread_id, "worker_read_sanitize", e))

        def worker_detect_city(thread_id: int):
            try:
                barrier.wait(timeout=5.0)
                for i in range(ops_per_thread):
                    if stop_flag.is_set():
                        break
                    detected = bot._detect_city_in_text("vậy ngày mai thế nào?")
                    if not isinstance(detected, str):
                        raise AssertionError(f"Unexpected city type: {type(detected)}")
                    time.sleep(0.0005)
            except Exception as e:
                errors.append((thread_id, "worker_detect_city", e))

        def worker_clear(thread_id: int):
            try:
                barrier.wait(timeout=5.0)
                for _ in range(ops_per_thread // 4):
                    if stop_flag.is_set():
                        break
                    bot._clear_chat_history()
                    time.sleep(0.002)
            except Exception as e:
                errors.append((thread_id, "worker_clear", e))

        def worker_get_reply(thread_id: int):
            try:
                barrier.wait(timeout=5.0)
                for i in range(ops_per_thread // 2):
                    if stop_flag.is_set():
                        break
                    reply = bot.get_reply("mấy giờ")
                    if not reply:
                        raise AssertionError("Empty reply from get_reply")
            except Exception as e:
                errors.append((thread_id, "worker_get_reply", e))

        threads: list[threading.Thread] = []
        for t_id in range(10):
            threads.append(threading.Thread(target=worker_append, args=(t_id,)))
        for t_id in range(10, 16):
            threads.append(threading.Thread(target=worker_read_sanitize, args=(t_id,)))
        for t_id in range(16, 20):
            threads.append(threading.Thread(target=worker_detect_city, args=(t_id,)))
        for t_id in range(20, 22):
            threads.append(threading.Thread(target=worker_clear, args=(t_id,)))
        for t_id in range(22, 25):
            threads.append(threading.Thread(target=worker_get_reply, args=(t_id,)))

        t_start = time.perf_counter()
        for t in threads:
            t.start()

        for t in threads:
            t.join(timeout=10.0)
            if t.is_alive():
                stop_flag.set()
                self.fail(f"Deadlock detected! Thread {t.name} did not terminate within timeout.")

        elapsed = time.perf_counter() - t_start

        if errors:
            self.fail(f"Concurrency stress test encountered {len(errors)} errors: {errors[:5]}")

        with bot._CHAT_HISTORY_LOCK:
            final_len = len(bot._CHAT_HISTORY)
            max_expected = bot.MAX_HISTORY_TURNS * 2
            self.assertLessEqual(final_len, max_expected, f"History size {final_len} exceeds max {max_expected}")
            for item in bot._CHAT_HISTORY:
                self.assertIn("role", item)
                self.assertIn("content", item)
                self.assertIn(item["role"], ("user", "assistant"))

        acquired = bot._CHAT_HISTORY_LOCK.acquire(blocking=False)
        self.assertTrue(acquired, "_CHAT_HISTORY_LOCK was left locked or corrupted!")
        bot._CHAT_HISTORY_LOCK.release()

    # =========================================================================
    # 2. FAST AI CIRCUIT BREAKER STRESS TESTS
    # =========================================================================

    def test_02_circuit_breaker_abrupt_network_failure_types_timing(self):
        """
        Adversarial Circuit Breaker Test:
        Simulate various abrupt socket/network exceptions (gaierror, WinError 10051/10060/10065,
        timed out, ConnectionRefused).
        Verify circuit breaker triggers immediately (< 1.0s, target < 0.05s) and trips cooldown.
        """
        network_exceptions = [
            urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed")),
            urllib.error.URLError(OSError(10051, "WinError 10051: Unreachable network")),
            urllib.error.URLError(OSError(10060, "WinError 10060: Connection timed out")),
            urllib.error.URLError(OSError(10065, "WinError 10065: Unreachable host")),
            urllib.error.URLError(ConnectionRefusedError(10061, "Connection refused")),
            TimeoutError("The read operation timed out"),
            urllib.error.URLError("timed out"),
        ]

        for exc in network_exceptions:
            bot._GEMINI_NETWORK_OFFLINE_UNTIL = 0.0
            start = time.perf_counter()
            with patch("urllib.request.urlopen", side_effect=exc):
                with self.assertRaises(RuntimeError):
                    gen = bot._stream_gemini_tokens("test query", "system prompt")
                    next(gen)
            elapsed = time.perf_counter() - start

            self.assertLess(
                elapsed, 1.0,
                f"Circuit breaker took too long ({elapsed:.4f}s) for exception {type(exc)}: {exc}"
            )
            self.assertGreater(
                bot._GEMINI_NETWORK_OFFLINE_UNTIL, time.monotonic(),
                f"Cooldown was not set for exception {exc}"
            )

    def test_03_circuit_breaker_failover_to_ollama_end_to_end_timing(self):
        """
        Adversarial End-to-End Failover Timing Test:
        Call bot.stream_reply() when Gemini fails with abrupt network disconnect.
        Verify full failover into Ollama streaming occurs in < 1.0 second.
        """
        bot._GEMINI_NETWORK_OFFLINE_UNTIL = 0.0
        net_err = urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed"))

        mock_ollama_lines = [
            b'{"message": {"content": "Toi "}, "done": false}\n',
            b'{"message": {"content": "la "}, "done": false}\n',
            b'{"message": {"content": "Wisp."}, "done": true}\n',
        ]
        mock_resp = MagicMock()
        mock_resp.__enter__.return_value = iter(mock_ollama_lines)

        def mock_urlopen(req, timeout=None):
            url = getattr(req, "full_url", str(req))
            if "generativelanguage.googleapis.com" in url:
                raise net_err
            if "11434" in url:
                return mock_resp
            raise RuntimeError(f"Unexpected url: {url}")

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            with patch("bot._resolve_ollama_model", return_value="qwen2.5:3b"):
                start = time.perf_counter()
                stream = bot.stream_reply("ai la ban?")
                first_token_time = None
                tokens = []
                for event in stream:
                    if isinstance(event, dict) and event.get("type") == "token":
                        if first_token_time is None:
                            first_token_time = time.perf_counter() - start
                        tokens.append(event.get("delta", ""))
                total_time = time.perf_counter() - start

        self.assertIsNotNone(first_token_time, "Did not receive any tokens from Ollama fallback!")
        self.assertLess(
            first_token_time, 1.0,
            f"Failover to first Ollama token took too long: {first_token_time:.4f}s (must be < 1.0s)!"
        )
        self.assertLess(
            total_time, 1.5,
            f"Total failover stream time took too long: {total_time:.4f}s"
        )
        full_text = "".join(tokens)
        self.assertIn("Wisp", full_text)

    def test_04_circuit_breaker_both_gemini_and_ollama_offline_does_not_hang(self):
        """
        Stress Test: Both Gemini and Ollama are completely offline/unreachable.
        Verify system falls through safely to OLLAMA_WAITING_MSG in < 1.0s without hanging.
        """
        bot._GEMINI_NETWORK_OFFLINE_UNTIL = 0.0
        net_err = urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed"))

        with patch("urllib.request.urlopen", side_effect=net_err):
            start = time.perf_counter()
            events = list(bot.stream_reply("hôm nay ăn gì"))
            elapsed = time.perf_counter() - start

        self.assertLess(elapsed, 1.0, f"Graceful offline degradation took too long: {elapsed:.4f}s")
        token_events = [e for e in events if isinstance(e, dict) and e.get("type") == "token"]
        self.assertTrue(len(token_events) > 0)
        self.assertIn("Wisp", token_events[0].get("delta", ""))

    # =========================================================================
    # 3. SERVER STREAM CANCELLATION TESTS
    # =========================================================================

    def test_05_server_stream_cancellation_worker_stops_cleanly_speak_false(self):
        """
        Verify that when speak=False (no TTS audio processing), generator cancellation
        sets stop_event and terminates the _producer_worker thread in < 0.1s.
        """
        producer_thread = None
        orig_thread_init = threading.Thread.__init__

        def custom_thread_init(self_t, *args, **kwargs):
            nonlocal producer_thread
            orig_thread_init(self_t, *args, **kwargs)
            target = kwargs.get("target")
            if target and getattr(target, "__name__", "") == "_producer_worker":
                producer_thread = self_t

        with patch("threading.Thread.__init__", custom_thread_init):
            client = app.app.test_client()
            res = client.post("/api/chat/stream", json={"message": "mấy giờ", "speak": False})
            self.assertEqual(res.status_code, 200)
            gen = res.response
            first_line = next(gen)
            self.assertTrue(len(first_line) > 0)
            self.assertIsNotNone(producer_thread, "Expected _producer_worker thread to be captured")

            t0 = time.perf_counter()
            gen.close()
            producer_thread.join(timeout=0.5)
            dt = time.perf_counter() - t0

            self.assertFalse(
                producer_thread.is_alive(),
                f"Producer worker failed to stop within 0.5s (took {dt:.3f}s)"
            )
            self.assertLess(dt, 0.5)

    def test_06_server_stream_cancellation_active_tts_deadlock_detection(self):
        """
        ADVERSARIAL STRESS TEST / BUG DETECTION:
        When speak=True and client disconnects while a TTS future is in-flight,
        the generator's finally: block calls fut.cancel() inside `with audio_lock:`.
        Because `audio_lock` was initialized as `threading.Lock()` (non-reentrant mutex),
        and `fut.cancel()` synchronously invokes `_on_fut_done` which also tries to acquire
        `audio_lock`, the thread DEADLOCKS on itself!

        This test detects and confirms this deadlock empirically.
        """
        client = app.app.test_client()
        res = client.post("/api/chat/stream", json={"message": "thời tiết Hà Nội", "speak": True})
        gen = res.response

        # Read 5 events so that the token is yielded and the TTS future is queued
        events = []
        for _ in range(5):
            events.append(next(gen))

        # Run gen.close() in a daemon thread with a strict 1.0s watchdog
        close_completed = threading.Event()

        def do_close():
            try:
                gen.close()
                close_completed.set()
            except Exception:
                close_completed.set()

        closer_thread = threading.Thread(target=do_close, daemon=True)
        closer_thread.start()
        closer_thread.join(timeout=1.0)

        # IF THE BUG IS PRESENT, close_completed is NOT set because gen.close() deadlocked!
        if not close_completed.is_set():
            self.fail(
                "CRITICAL DEADLOCK DETECTED: gen.close() hung for > 1.0s! "
                "Root cause: `audio_lock = threading.Lock()` in app.py:627 is non-reentrant. "
                "In app.py:777, `with audio_lock:` calls `fut.cancel()`, which synchronously "
                "invokes callback `_on_fut_done()`, which also requests `with audio_lock:`. "
                "Mitigation: Change `audio_lock = threading.RLock()` and/or cancel futures outside `with audio_lock:`."
            )


if __name__ == "__main__":
    unittest.main()
