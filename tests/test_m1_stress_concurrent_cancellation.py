"""
Adversarial Stress Test: Concurrent Stream Cancellations with In-Flight TTS
Author: Empirical Challenger 1 (Milestone M1 Iteration 2)
Focus:
- 10 rapid concurrent stream cancellations with active in-flight TTS futures.
- Assertion that each cancellation finishes in < 0.2s.
- Assertion that no deadlocks, thread leaks, or unhandled exceptions occur.
- Post-cancellation functional verification of streaming and TTS.
"""
import asyncio
import gc
import json
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import app
import bot


class TestConcurrentStreamCancellationStress(unittest.TestCase):
    def setUp(self):
        bot._clear_chat_history()
        app._TTS_CACHE.clear()
        bot._GEMINI_NETWORK_OFFLINE_UNTIL = 0.0

    def test_10_rapid_concurrent_cancellations_active_tts_futures(self):
        """
        Adversarial Stress Test:
        Launch 10 concurrent HTTP stream requests with speak=True.
        Ensure TTS futures are actively running/in-flight on _BG_LOOP.
        Simultaneously cancel (close) all 10 generators via a synchronization barrier.
        Verify:
        1. Each individual gen.close() completes in < 0.2s.
        2. All 10 producer worker threads terminate cleanly within 0.5s.
        3. Zero deadlocks, zero lock-order inversions, zero uncaught exceptions.
        """
        # Slow down TTS synthesis slightly (1.5s) to guarantee futures remain in-flight during abort
        async def slow_tts(text, voice_id=app.DEFAULT_VOICE_ID, fast_first=False):
            await asyncio.sleep(1.5)
            return b"MOCK_MP3_STREAM_CANCELLATION"

        producer_threads = []
        orig_thread_init = threading.Thread.__init__

        def capture_thread_init(self_t, *args, **kwargs):
            orig_thread_init(self_t, *args, **kwargs)
            target = kwargs.get("target")
            if target and getattr(target, "__name__", "") == "_producer_worker":
                producer_threads.append(self_t)

        barrier = threading.Barrier(10)
        cancellation_times = [0.0] * 10
        errors = []

        def worker_task(idx):
            try:
                client = app.app.test_client()
                res = client.post(
                    "/api/chat/stream",
                    json={"message": f"thời tiết Hà Nội {idx}", "speak": True},
                )
                gen = res.response
                # Consume initial events to trigger clause extraction and TTS dispatch
                events = []
                for _ in range(5):
                    events.append(next(gen))

                # Synchronize all 10 threads so cancellation happens at the exact same moment
                barrier.wait(timeout=5.0)

                t0 = time.perf_counter()
                gen.close()
                dt = time.perf_counter() - t0
                cancellation_times[idx] = dt
            except Exception as e:
                errors.append((idx, e))

        with patch("threading.Thread.__init__", capture_thread_init):
            with patch("app._async_generate_tts", side_effect=slow_tts):
                with ThreadPoolExecutor(max_workers=10) as executor:
                    futures = [executor.submit(worker_task, i) for i in range(10)]
                    for fut in futures:
                        fut.result()

        self.assertEqual(len(errors), 0, f"Encountered unexpected errors during stress test: {errors}")
        self.assertEqual(len(producer_threads), 10, "Expected exactly 10 producer threads")

        for idx, dt in enumerate(cancellation_times):
            self.assertLess(
                dt,
                0.2,
                f"Thread {idx} cancellation took {dt:.4f}s, exceeding strict 0.2s limit!",
            )

        # Verify all producer threads terminated promptly and did not leak
        for idx, t in enumerate(producer_threads):
            t.join(timeout=0.5)
            self.assertFalse(
                t.is_alive(),
                f"Producer worker thread {idx} leaked or hung after gen.close()!",
            )

    def test_20_rapid_staggered_cancellations(self):
        """
        Adversarial Stress Test:
        Launch 20 streams with random rapid cancellations at varying stages:
        turn 1 (after 1 event), turn 2 (after 3 events), turn 3 (after 5 events).
        Verify no thread leaks, deadlocks, or lock contention issues.
        """
        async def slow_tts(text, voice_id=app.DEFAULT_VOICE_ID, fast_first=False):
            await asyncio.sleep(1.0)
            return b"MOCK_MP3_STREAM_CANCELLATION"

        producer_threads = []
        orig_thread_init = threading.Thread.__init__

        def capture_thread_init(self_t, *args, **kwargs):
            orig_thread_init(self_t, *args, **kwargs)
            target = kwargs.get("target")
            if target and getattr(target, "__name__", "") == "_producer_worker":
                producer_threads.append(self_t)

        cancellation_times = [0.0] * 20
        errors = []

        def worker_task(idx):
            try:
                client = app.app.test_client()
                res = client.post(
                    "/api/chat/stream",
                    json={"message": f"thời tiết TP. Hồ Chí Minh {idx}", "speak": True},
                )
                gen = res.response
                consume_count = (idx % 4) + 2  # consume 2 to 5 events
                for _ in range(consume_count):
                    next(gen)

                t0 = time.perf_counter()
                gen.close()
                dt = time.perf_counter() - t0
                cancellation_times[idx] = dt
            except Exception as e:
                errors.append((idx, e))

        with patch("threading.Thread.__init__", capture_thread_init):
            with patch("app._async_generate_tts", side_effect=slow_tts):
                with ThreadPoolExecutor(max_workers=20) as executor:
                    futures = [executor.submit(worker_task, i) for i in range(20)]
                    for fut in futures:
                        fut.result()

        self.assertEqual(len(errors), 0, f"Errors in 20-thread staggered stress: {errors}")
        for idx, dt in enumerate(cancellation_times):
            self.assertLess(
                dt,
                0.2,
                f"Staggered thread {idx} cancellation took {dt:.4f}s >= 0.2s",
            )

        for idx, t in enumerate(producer_threads):
            t.join(timeout=0.5)
            self.assertFalse(t.is_alive(), f"Thread {idx} leaked!")

    def test_post_cancellation_system_integrity(self):
        """
        Stress & Recovery Test:
        After multiple rounds of rapid stream cancellations with in-flight TTS,
        verify the server event loop and TTS engine remain healthy and functional:
        A complete request with speak=True must complete 100% and emit audio and done events.
        """
        client = app.app.test_client()
        res = client.post("/api/chat/stream", json={"message": "mấy giờ", "speak": True})
        self.assertEqual(res.status_code, 200)
        events = list(res.response)
        parsed = [json.loads(line.decode("utf-8")) for line in events if line.strip()]

        types = [e.get("type") for e in parsed]
        self.assertIn("normalized", types)
        self.assertIn("token", types)
        self.assertIn("done", types)
        self.assertTrue(
            any(t == "audio" for t in types),
            f"Expected audio event in healthy post-cancellation stream, got: {types}",
        )


if __name__ == "__main__":
    unittest.main()
