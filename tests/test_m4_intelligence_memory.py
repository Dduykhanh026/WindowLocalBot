"""
Milestone M4 Verification Suite — Trí nhớ Dài hạn (SQLite), Suy luận Thích ứng,
Dự báo Thời tiết 7 Ngày, Web Search Grounding & Trung tâm Bộ nhớ 3 Tab (E2E Edge).
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
from datetime import datetime
from unittest.mock import patch
from werkzeug.serving import make_server

import app as app_module
import bot
import memory_store
import weather


def _find_headless_chromium_binary() -> str | None:
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        shutil.which("msedge"),
        shutil.which("chrome"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


class _FakeHttpResponse:
    def __init__(self, payload: dict | str | bytes, lines: list[bytes] | None = None):
        if isinstance(payload, dict):
            self._bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        elif isinstance(payload, str):
            self._bytes = payload.encode("utf-8")
        else:
            self._bytes = payload
        self._lines = lines

    def read(self) -> bytes:
        return self._bytes

    def __iter__(self):
        if self._lines is not None:
            return iter(self._lines)
        return iter(self._bytes.splitlines(keepends=True))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _M4IsolatedBaseTestCase(unittest.TestCase):
    """Đảm bảo mọi bài test M4 chạy trên SQLite tạm thời và khôi phục nguyên trạng Hồ sơ & Lịch sử."""

    def setUp(self) -> None:
        self._orig_db_path = memory_store.get_db_path()
        self._tmp_dir = tempfile.mkdtemp(prefix="wisp_m4_test_")
        self._test_db_path = os.path.join(self._tmp_dir, "test_wisp_memory.db")
        memory_store.set_db_path(self._test_db_path)

        self._orig_profile = dict(bot.get_user_profile())
        self._profile_file_existed = os.path.exists(bot.USER_PROFILE_FILE)
        self._profile_raw_backup = None
        if self._profile_file_existed:
            with open(bot.USER_PROFILE_FILE, "rb") as f:
                self._profile_raw_backup = f.read()

        bot.update_user_profile(
            {
                "name": "Nguyên",
                "gender": "Nam",
                "age": "26",
                "height_cm": "172",
                "weight_kg": "66",
                "city": "TP. Hồ Chí Minh",
                "job": "iOS/SwiftUI & Flutter Developer",
                "notes": "",
                "auto_learn": True,
            }
        )
        bot.clear_chat_history()

    def tearDown(self) -> None:
        bot.wait_for_background_memory(timeout=2.5)
        bot.clear_chat_history()
        memory_store.set_db_path(self._orig_db_path)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

        if self._profile_file_existed and self._profile_raw_backup is not None:
            with open(bot.USER_PROFILE_FILE, "wb") as f:
                f.write(self._profile_raw_backup)
            bot._USER_PROFILE_CACHE = None
            bot.get_user_profile()
        else:
            if os.path.exists(bot.USER_PROFILE_FILE):
                try:
                    os.remove(bot.USER_PROFILE_FILE)
                except OSError:
                    pass
            bot._USER_PROFILE_CACHE = dict(self._orig_profile)


class TestM4MemoryStoreEngine(_M4IsolatedBaseTestCase):
    def test_01_sqlite_schema_crud_and_unittest_isolation(self):
        self.assertNotEqual(
            os.path.abspath(memory_store.get_db_path()),
            os.path.abspath(memory_store.PROD_DB_PATH),
        )

        m1 = memory_store.upsert_memory(
            "Đang phát triển ứng dụng SwiftUI tên FitNote",
            category="project",
            importance=4,
            pinned=True,
            source="manual",
        )
        self.assertIsNotNone(m1)
        self.assertEqual(m1["category"], "project")
        self.assertTrue(m1["pinned"])

        t1 = memory_store.add_task(
            "Nộp bản build TestFlight",
            due_text="Thứ Hai (02/11) 09:00",
            due_iso="2026-11-02T09:00",
            priority=3,
        )
        self.assertIsNotNone(t1)
        self.assertEqual(t1["status"], "pending")

        s1 = memory_store.add_episodic_summary("Đầu buổi người dùng hỏi về kiến trúc MVVM trong FitNote.")
        self.assertIsNotNone(s1)

        counts = memory_store.get_counts()
        self.assertEqual(counts["memories_total"], 1)
        self.assertEqual(counts["pinned_total"], 1)
        self.assertEqual(counts["pending_tasks"], 1)

        updated_m1 = memory_store.update_memory(m1["id"], content="Đang phát triển FitNote trên iOS 18", pinned=False)
        self.assertEqual(updated_m1["content"], "Đang phát triển FitNote trên iOS 18")
        self.assertFalse(updated_m1["pinned"])

        done_t1 = memory_store.update_task_status(t1["id"], "done")
        self.assertEqual(done_t1["status"], "done")
        self.assertTrue(bool(done_t1["completed_at"]))

        self.assertTrue(memory_store.delete_memory(m1["id"]))
        self.assertTrue(memory_store.delete_task(t1["id"]))
        self.assertEqual(memory_store.clear_episodic_summaries(), 1)
        self.assertEqual(memory_store.get_counts()["memories_total"], 0)
        self.assertEqual(memory_store.get_counts()["tasks_total"], 0)

    def test_02_vietnamese_lexical_dedup_upsert_and_distinct_entities(self):
        first = memory_store.upsert_memory("Đang làm dự án SwiftUI tên FitNote", category="auto")
        self.assertEqual(first["category"], "project")

        # Cùng chủ đề mở rộng thêm chi tiết -> gộp/cập nhật thay vì sinh bản ghi trùng
        expanded = memory_store.upsert_memory(
            "Đang làm dự án SwiftUI tên FitNote tích hợp HealthKit",
            category="project",
            importance=5,
        )
        self.assertEqual(expanded["id"], first["id"])
        self.assertIn("HealthKit", expanded["content"])
        self.assertEqual(expanded["importance"], 5)
        self.assertEqual(len(memory_store.list_memories()), 1)

        # Dự án khác tên thực thể (ShopGo != FitNote) -> bắt buộc tách bản ghi độc lập
        second = memory_store.upsert_memory("Đang làm dự án Flutter tên ShopGo", category="auto")
        self.assertNotEqual(second["id"], first["id"])
        self.assertEqual(len(memory_store.list_memories()), 2)

        # Xóa ký ức bằng truy vấn không dấu
        forgotten = memory_store.forget_memories_by_query("du an fitnote")
        self.assertEqual(len(forgotten), 1)
        self.assertEqual(forgotten[0]["id"], first["id"])
        remaining = memory_store.list_memories()
        self.assertEqual(len(remaining), 1)
        self.assertIn("ShopGo", remaining[0]["content"])

    def test_03_max_300_cap_prunes_unpinned_low_importance_preserving_pinned(self):
        pinned_ids = set()
        for i in range(4):
            pm = memory_store.upsert_memory(
                f"Ký ức quan trọng đã ghim độc nhất mã PinAlpha{i}x99",
                category="project",
                importance=2,
                pinned=True,
            )
            pinned_ids.add(pm["id"])

        for idx in range(306):
            memory_store.upsert_memory(
                f"Ghi chú sự kiện riêng biệt số SerialItem{idx:04d} chủ đề Topic{idx:04d}",
                category="fact",
                importance=1 if idx < 50 else 3,
                pinned=False,
            )

        all_mems = memory_store.list_memories(limit=400)
        self.assertLessEqual(len(all_mems), memory_store.MAX_ACTIVE_MEMORIES)
        surviving_ids = {m["id"] for m in all_mems}
        self.assertTrue(pinned_ids.issubset(surviving_ids), "Pinned memories must NEVER be pruned!")

    def test_04_parse_vietnamese_due_time_natural_expressions(self):
        # Thứ Bảy, 31/10/2026 10:00 +07:00
        base_now = datetime(2026, 10, 31, 10, 0, tzinfo=memory_store.VN_TZ)

        due_txt, due_iso = memory_store.parse_vietnamese_due_time("chiều nay gọi cho khách hàng", now_dt=base_now)
        self.assertTrue(due_iso.startswith("2026-10-31T15:00"), f"got {due_iso}")
        self.assertIn("31/10", due_txt)

        due_txt2, due_iso2 = memory_store.parse_vietnamese_due_time("sáng mai 9h họp sprint", now_dt=base_now)
        self.assertEqual(due_iso2, "2026-11-01T09:00")
        self.assertIn("09:00", due_txt2)

        _, due_iso3 = memory_store.parse_vietnamese_due_time("ngày mốt gửi báo cáo", now_dt=base_now)
        self.assertTrue(due_iso3.startswith("2026-11-02"))

        _, due_iso4 = memory_store.parse_vietnamese_due_time("sáng thứ Hai nộp bản build TestFlight", now_dt=base_now)
        self.assertEqual(due_iso4, "2026-11-02T08:00")

        _, due_iso5 = memory_store.parse_vietnamese_due_time("15h30 ngày 5/11 khám răng", now_dt=base_now)
        self.assertEqual(due_iso5, "2026-11-05T15:30")

        # Câu thói quen lặp lại không được nhận nhầm thành lịch hẹn một lần
        habit_txt, habit_iso = memory_store.parse_vietnamese_due_time("mỗi tối thường đọc sách 30 phút", now_dt=base_now)
        self.assertEqual(habit_txt, "")
        self.assertEqual(habit_iso, "")

    def test_05_retrieve_context_bundle_hybrid_ranking_and_unaccented_vietnamese(self):
        base_now = datetime(2026, 10, 31, 10, 0, tzinfo=memory_store.VN_TZ)
        m_pin = memory_store.upsert_memory("Luôn ưu tiên viết Swift Concurrency async/await chuẩn Swift 6", category="skill", pinned=True)
        m_proj = memory_store.upsert_memory("Đang làm dự án SwiftUI tên FitNote theo dõi tập luyện", category="project", importance=4)
        m_coffee = memory_store.upsert_memory("Không uống được cà phê buổi tối vì dễ mất ngủ", category="health", importance=4)
        memory_store.upsert_memory("Thích nghe nhạc jazz nhẹ khi trời mưa", category="preference", importance=2)

        t_soon = memory_store.add_task("Nộp bản build TestFlight", due_text="Thứ Hai (02/11) 08:00", due_iso="2026-11-02T08:00", now_dt=base_now)
        t_later = memory_store.add_task("Gia hạn Apple Developer Program", due_text="15/11", due_iso="2026-11-15", now_dt=base_now)
        t_done = memory_store.add_task("Cài đặt Xcode mới", now_dt=base_now)
        memory_store.update_task_status(t_done["id"], "done", now_dt=base_now)

        # Truy vấn không dấu tiếng Việt
        bundle = memory_store.retrieve_context_bundle(
            "toi nay uong ca phe code du an fitnote duoc khong",
            now_dt=base_now,
            max_memories=3,
            max_tasks=4,
        )
        top_ids = [m["id"] for m in bundle["memories"]]
        self.assertIn(m_pin["id"], top_ids, "Pinned memory must always be included in context bundle")
        self.assertIn(m_proj["id"], top_ids)
        self.assertIn(m_coffee["id"], top_ids)

        task_ids = [t["id"] for t in bundle["tasks"]]
        self.assertEqual(task_ids[0], t_soon["id"], "Soonest due pending task must rank first")
        self.assertIn(t_later["id"], task_ids)
        self.assertNotIn(t_done["id"], task_ids, "Completed tasks must not clutter active prompt context")


class TestM4TwoTierLearningAndCompaction(_M4IsolatedBaseTestCase):
    def test_06_tier1_instant_task_reminder_complete_forget_and_explicit_remember(self):
        base_now = datetime(2026, 10, 31, 10, 0, tzinfo=memory_store.VN_TZ)

        # 1. Nhắc việc tức thì
        ev_task = bot.extract_instant_memory_and_tasks(
            "Nhắc mình sáng thứ Hai nộp bản build TestFlight",
            now_dt=base_now,
        )
        self.assertEqual(len(ev_task["tasks_added"]), 1)
        self.assertIn("TestFlight", ev_task["tasks_added"][0]["title"])
        self.assertEqual(ev_task["tasks_added"][0]["due_iso"], "2026-11-02T08:00")
        self.assertIn("📌", ev_task["direct_reply"])

        # 2. Hoàn thành việc tức thì
        ev_done = bot.extract_instant_memory_and_tasks(
            "Xong việc nộp bản build TestFlight rồi nhé",
            now_dt=base_now,
        )
        self.assertEqual(len(ev_done["tasks_completed"]), 1)
        self.assertEqual(memory_store.get_counts()["pending_tasks"], 0)
        self.assertIn("✅", ev_done["direct_reply"])

        # 3. Ghi nhớ tường minh -> lưu vào SQLite memories (không nhồi vào profile['notes'])
        ev_mem = bot.extract_instant_memory_and_tasks(
            "Ghi nhớ: Mình chuyên kiến trúc Clean Architecture + Riverpod cho Flutter",
            now_dt=base_now,
        )
        self.assertEqual(len(ev_mem["memories_added"]), 1)
        self.assertEqual(bot.get_user_profile().get("notes", ""), "")
        self.assertIn("🧠", ev_mem["direct_reply"])

        # 4. Lệnh quên đi
        ev_forget = bot.extract_instant_memory_and_tasks("Quên chuyện Riverpod cho Flutter đi nhé")
        self.assertEqual(len(ev_forget["memories_forgotten"]), 1)
        self.assertEqual(memory_store.get_counts()["memories_total"], 0)

    def test_07_tier1_compound_first_person_sharing_and_adversarial_immunity(self):
        ev = bot.extract_instant_memory_and_tasks(
            "Mình đang làm dự án SwiftUI tên FitNote và không uống được cà phê buổi tối"
        )
        self.assertEqual(len(ev["memories_added"]), 2)
        contents = " | ".join(m["content"] for m in memory_store.list_memories())
        self.assertIn("FitNote", contents)
        self.assertIn("cà phê", contents.lower())

        # Adversarial 1: Nội dung nằm trong code block tuyệt đối không được học nhầm
        before_cnt = memory_store.get_counts()["memories_total"]
        bot.extract_instant_memory_and_tasks("```swift\n// Mình thích uống trà sữa trân châu\nlet x = 1\n```")
        self.assertEqual(memory_store.get_counts()["memories_total"], before_cnt)

        # Adversarial 2: Ngôi thứ 3 hoặc quan hệ sở hữu của người khác không được học nhầm vào chủ nhân
        bot.extract_instant_memory_and_tasks("Bạn thân của mình thích ăn sầu riêng và anh ấy đang làm dự án CryptoWallet")
        self.assertEqual(memory_store.get_counts()["memories_total"], before_cnt)

    def test_08_tier2_background_ai_json_extraction_and_ollama_fallback(self):
        old_mem = memory_store.upsert_memory("Thích dùng Provider cho state management trong Flutter", category="preference")
        pending_task = memory_store.add_task("Gửi file thiết kế Figma cho team")

        # Giả lập Gemini offline -> _call_llm_json tự động fallback sang Ollama format="json"
        ollama_json_reply = {
            "message": {
                "content": json.dumps(
                    {
                        "ops": [
                            {
                                "op": "update_memory",
                                "id": old_mem["id"],
                                "category": "preference",
                                "content": "Đã chuyển từ Provider sang dùng Riverpod 2.0 cho Flutter",
                                "importance": 5,
                            },
                            {
                                "op": "add_memory",
                                "category": "habit",
                                "content": "Thường chạy bộ 5km quanh công viên vào sáng thứ Bảy",
                                "importance": 3,
                            },
                            {
                                "op": "complete_task",
                                "id": pending_task["id"],
                            },
                        ]
                    },
                    ensure_ascii=False,
                )
            }
        }

        captured_ollama_bodies = []

        def _fake_urlopen(req, timeout=5.0):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if "generativelanguage.googleapis.com" in url:
                raise urllib.error.URLError("simulated offline")
            if url.endswith("/api/tags"):
                return _FakeHttpResponse({"models": [{"name": "qwen2.5:3b"}]})
            if url.endswith("/api/chat"):
                captured_ollama_bodies.append(json.loads(req.data.decode("utf-8")))
                return _FakeHttpResponse(ollama_json_reply)
            raise AssertionError(f"Unexpected URL in Tier-2 test: {url}")

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            applied = bot._run_background_memory_learning_sync(
                "Dạo này mình chuyển hẳn từ Provider sang Riverpod 2.0 rồi, sáng thứ Bảy hay chạy bộ 5km, à mình cũng gửi xong file Figma rồi."
            )

        self.assertEqual(len(applied), 3)
        self.assertTrue(captured_ollama_bodies and captured_ollama_bodies[0].get("format") == "json")

        mems = {m["id"]: m for m in memory_store.list_memories()}
        self.assertIn("Riverpod 2.0", mems[old_mem["id"]]["content"])
        self.assertTrue(any("chạy bộ 5km" in m["content"] for m in mems.values()))
        tasks = {t["id"]: t for t in memory_store.list_tasks()}
        self.assertEqual(tasks[pending_task["id"]]["status"], "done")

        # Tắt auto_learn -> Tầng 2 không được phép tự ghi thêm
        bot.update_user_profile({"auto_learn": False})
        with patch.object(bot, "_call_llm_json") as mock_llm:
            res_disabled = bot._run_background_memory_learning_sync("Mình thích uống matcha đá xay")
            self.assertEqual(res_disabled, [])
            mock_llm.assert_not_called()

    def test_09_rolling_episodic_compaction_over_10_turns_and_unified_context(self):
        memory_store.upsert_memory("Đang xây dựng ứng dụng FitNote bằng SwiftUI", category="project", pinned=True)
        memory_store.add_task("Nộp bản build TestFlight", due_text="Thứ Hai (02/11) 08:30", due_iso="2026-11-02T08:30")

        with patch.object(
            bot,
            "_call_llm_json",
            return_value={"summary": "Đầu phiên, Nguyên đã bàn về cấu trúc database CoreData và đồng bộ CloudKit cho app FitNote."},
        ):
            for turn_idx in range(12):
                bot._append_chat_history(
                    f"Câu hỏi kỹ thuật lượt {turn_idx + 1} về CoreData CloudKit",
                    f"Phản hồi kỹ thuật lượt {turn_idx + 1}",
                )
            bot.wait_for_background_memory(timeout=3.0)

        # Cửa sổ hội thoại ngắn hạn giữ đúng 10 lượt (20 message)
        self.assertEqual(len(bot.get_chat_history()), bot.MAX_HISTORY_TURNS * 2)
        summaries = memory_store.list_episodic_summaries()
        self.assertGreaterEqual(len(summaries), 1)
        self.assertIn("CloudKit", summaries[0]["summary"])

        # Kiểm tra _build_system_context hợp nhất đầy đủ Hồ sơ + Ký ức + Việc cần nhớ + Tóm tắt phiên
        sys_ctx = bot._build_system_context(
            text_norm="ke hoach code fitnote tuan nay",
            raw_message="Kế hoạch code FitNote tuần này thế nào?",
        )
        self.assertIn("Nguyên", sys_ctx)
        self.assertIn("FitNote", sys_ctx)
        self.assertIn("TestFlight", sys_ctx)
        self.assertIn("CloudKit", sys_ctx)


class TestM4ClearHistoryIsolationAndRestApi(_M4IsolatedBaseTestCase):
    def test_10_clear_history_strictly_preserves_memories_tasks_and_profile(self):
        memory_store.upsert_memory("Đang làm dự án SwiftUI tên FitNote", category="project", pinned=True)
        memory_store.upsert_memory("Không uống được cà phê buổi tối", category="health")
        memory_store.add_task("Nộp bản build TestFlight", due_text="Thứ Hai (02/11) 08:30", due_iso="2026-11-02T08:30")
        memory_store.add_episodic_summary("Tóm tắt tạm của phiên chat hiện tại.")
        bot._append_chat_history("Xin chào", "Chào Nguyên!")

        # 1. Xóa qua bot.clear_chat_history()
        bot.clear_chat_history()
        self.assertEqual(bot.get_chat_history(), [])
        self.assertEqual(memory_store.list_episodic_summaries(), [])
        self.assertEqual(memory_store.get_counts()["memories_total"], 2)
        self.assertEqual(memory_store.get_counts()["pending_tasks"], 1)

        # 2. Xóa qua lệnh chat "xóa lịch sử"
        bot._append_chat_history("Câu chat mới", "Trả lời mới")
        reply_clear = bot.get_reply("xóa lịch sử")
        self.assertIn("🧹", reply_clear)
        self.assertEqual(bot.get_chat_history(), [])
        self.assertEqual(memory_store.get_counts()["memories_total"], 2)
        self.assertEqual(memory_store.get_counts()["pending_tasks"], 1)

        # 3. Xóa qua HTTP DELETE /api/history
        client = app_module.app.test_client()
        bot._append_chat_history("Lượt chat trước khi bấm nút Xóa", "Ok")
        memory_store.add_episodic_summary("Tóm tắt phiên lần 2")
        res = client.delete("/api/history")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(bot.get_chat_history(), [])
        self.assertEqual(memory_store.list_episodic_summaries(), [])
        self.assertEqual(memory_store.get_counts()["memories_total"], 2)
        self.assertEqual(memory_store.get_counts()["pending_tasks"], 1)
        self.assertEqual(bot.get_user_profile()["name"], "Nguyên")

        # 4. Ngay cả khi mất mạng hoàn toàn sau khi xóa sạch chat, hỏi lại vẫn nhớ chính xác FitNote & TestFlight!
        with patch.object(bot, "_ask_gemini", return_value=None), patch.object(bot, "_ask_ollama", return_value=None):
            recall_reply = bot.get_reply("Dự án mình đang làm tên gì và thứ Hai mình có việc gì cần làm?")
        self.assertIn("FitNote", recall_reply)
        self.assertIn("TestFlight", recall_reply)

    def test_11_memories_rest_api_crud_and_friendly_vietnamese_errors(self):
        client = app_module.app.test_client()

        # POST memory
        r_mem = client.post(
            "/api/memories",
            json={"kind": "memory", "content": "Thích cà phê cold brew ít đường", "category": "preference"},
        )
        self.assertEqual(r_mem.status_code, 200)
        mem_data = r_mem.get_json()
        self.assertTrue(mem_data["ok"])
        mem_id = mem_data["item"]["id"]

        # POST task (tự phân tích due_text tiếng Việt)
        r_task = client.post(
            "/api/memories",
            json={"kind": "task", "title": "Sáng mai 9h review PR của team"},
        )
        self.assertEqual(r_task.status_code, 200)
        task_data = r_task.get_json()
        self.assertTrue(task_data["ok"])
        task_id = task_data["item"]["id"]
        self.assertIn("09:00", task_data["item"]["due_text"])

        # GET /api/memories
        r_get = client.get("/api/memories")
        self.assertEqual(r_get.status_code, 200)
        listing = r_get.get_json()
        self.assertEqual(listing["counts"]["memories_total"], 1)
        self.assertEqual(listing["counts"]["pending_tasks"], 1)

        # PATCH pin memory
        r_pin = client.patch(f"/api/memories/memory/{mem_id}", json={"pinned": True})
        self.assertEqual(r_pin.status_code, 200)
        self.assertTrue(r_pin.get_json()["item"]["pinned"])
        self.assertEqual(r_pin.get_json()["counts"]["pinned_total"], 1)

        # PATCH complete task
        r_done = client.patch(f"/api/memories/task/{task_id}", json={"status": "done"})
        self.assertEqual(r_done.status_code, 200)
        self.assertEqual(r_done.get_json()["item"]["status"], "done")
        self.assertEqual(r_done.get_json()["counts"]["pending_tasks"], 0)

        # Validation & 404 friendly Vietnamese errors (no Python traceback)
        r_empty = client.post("/api/memories", json={"kind": "memory", "content": "   "})
        self.assertEqual(r_empty.status_code, 400)
        self.assertNotIn("Traceback", r_empty.get_data(as_text=True))

        r_404 = client.delete("/api/memories/memory/999999")
        self.assertEqual(r_404.status_code, 404)
        self.assertNotIn("Traceback", r_404.get_data(as_text=True))

        # Bulk delete memories only (preserving tasks)
        r_clear_mems = client.delete("/api/memories?kind=memories")
        self.assertEqual(r_clear_mems.status_code, 200)
        self.assertEqual(memory_store.get_counts()["memories_total"], 0)
        self.assertEqual(memory_store.get_counts()["tasks_total"], 1)


class TestM4AdaptiveThinkingWeather7DAndWebGrounding(_M4IsolatedBaseTestCase):
    def test_12_adaptive_thinking_router_and_deep_solving_orb_state(self):
        # 1. Voice Mode luôn khóa thinking_budget = 0 để phản xạ tức thì
        plan_voice = bot._classify_reasoning_plan(
            "Phân tích kiến trúc Clean Architecture kết hợp MVVM trong SwiftUI",
            is_voice=True,
        )
        self.assertEqual(plan_voice["thinking_budget"], 0)
        self.assertEqual(plan_voice["preferred_model"], "gemini-3.1-flash-lite")

        # 2. Câu ngắn thường ngày -> thinking_budget = 0
        plan_short = bot._classify_reasoning_plan("Chiều nay uống nước ép gì mát nhỉ?", is_voice=False)
        self.assertEqual(plan_short["thinking_budget"], 0)

        # 3. Bài toán kiến trúc / debug / code block -> thinking_budget = 2048 (4096 ở chế độ detailed)
        complex_q = "Phân tích cách xử lý race condition và actor isolation với Swift Concurrency trong kiến trúc MVVM"
        plan_deep = bot._classify_reasoning_plan(complex_q, prefs={"style": "balanced"}, is_voice=False)
        self.assertEqual(plan_deep["thinking_budget"], 2048)
        self.assertEqual(plan_deep["preferred_model"], "gemini-3.5-flash")

        cfg_detailed = bot._build_gemini_generation_config(complex_q, prefs={"style": "detailed"}, is_voice=False)
        self.assertEqual(cfg_detailed["thinkingConfig"]["thinkingBudget"], 4096)

        # 4. stream_reply phát trạng thái Orb "solving" ("Đang suy luận chuyên sâu…") khi thinking_budget > 0
        with patch.object(bot, "_stream_gemini_tokens", return_value=iter(["Giải pháp dùng `@MainActor` và `actor` riêng."])):
            events = list(bot.stream_reply(complex_q, prefs={"style": "balanced"}, is_voice=False))
        state_events = [e for e in events if e.get("type") == "state"]
        self.assertTrue(
            any(e.get("state") == "solving" and "suy luận chuyên sâu" in e.get("label", "").lower() for e in state_events),
            f"Expected deep solving Orb state, got: {state_events}",
        )

    def test_13_multi_day_7d_weather_and_vietnamese_day_offsets(self):
        fake_loc = {"name": "Huế", "country": "Việt Nam", "latitude": 16.4637, "longitude": 107.5909}
        # Giả lập 7 ngày bắt đầu từ Thứ Bảy 31/10/2026
        fake_daily_7d = {
            "daily": {
                "time": [
                    "2026-10-31",
                    "2026-11-01",
                    "2026-11-02",
                    "2026-11-03",
                    "2026-11-04",
                    "2026-11-05",
                    "2026-11-06",
                ],
                "weather_code": [1, 2, 61, 3, 0, 80, 1],
                "temperature_2m_max": [30.0, 29.5, 27.0, 28.0, 31.0, 28.5, 30.2],
                "temperature_2m_min": [23.0, 22.5, 21.5, 22.0, 23.0, 22.0, 22.8],
                "precipitation_probability_max": [15, 20, 75, 30, 10, 65, 15],
            }
        }

        base_sat = datetime(2026, 10, 31, 9, 0, tzinfo=bot.VN_TZ)

        # Kiểm tra phân tích mốc ngày tiếng Việt:
        # Hôm nay là Thứ Bảy (31/10) -> "cuối tuần này" = Thứ Bảy (0) + Chủ Nhật (1)
        q_weekend = "thoi tiet hue cuoi tuan nay"
        plan_weekend = bot._parse_weather_day_plan(q_weekend, bot._norm(q_weekend), now_dt=base_sat)
        self.assertEqual(plan_weekend["mode"], "multi")
        self.assertEqual(plan_weekend["target_offsets"], [0, 1])

        # "3 ngày tới" -> days=3, target_offsets=[0, 1, 2]
        q_3d = "du bao thoi tiet da nang 3 ngay toi"
        plan_3d = bot._parse_weather_day_plan(q_3d, bot._norm(q_3d), now_dt=base_sat)
        self.assertEqual(plan_3d["mode"], "multi")
        self.assertEqual(plan_3d["days"], 3)
        self.assertEqual(plan_3d["target_offsets"], [0, 1, 2])

        # "ngày mốt" -> target_offsets=[2]
        q_day_after = "thoi tiet ha noi ngay mot"
        plan_day_after = bot._parse_weather_day_plan(q_day_after, bot._norm(q_day_after), now_dt=base_sat)
        self.assertEqual(plan_day_after["target_offsets"], [2])

        # Kiểm tra _extract_city_after lọc sạch cụm thời gian đính kèm sau tên thành phố
        extracted_city = bot._extract_city_after("thoi tiet o hue cuoi tuan nay", ("o", "tai"))
        self.assertEqual(extracted_city, "hue")

        with patch.object(weather, "geocode", return_value=fake_loc), patch.object(
            weather, "_get_json", return_value=fake_daily_7d
        ):
            res_weekend = weather.get_multi_day_weather("Huế", days=7, target_offsets=[0, 1])
            self.assertIn("tại Huế:", res_weekend)
            self.assertIn("31/10", res_weekend)
            self.assertIn("01/11", res_weekend)
            self.assertNotIn("04/11", res_weekend)

            res_single = weather.get_multi_day_weather("Huế", days=7, target_offsets=[2])
            self.assertIn("Ngày mốt", res_single)
            self.assertIn("75%", res_single)

            # Lệnh thời tiết ngắn "thời tiết Huế 3 ngày tới" trả thẳng bảng nhiều ngày chuẩn xác
            fast_reply = bot.get_reply("thời tiết Huế 3 ngày tới")
            self.assertIn("Dự báo", fast_reply)
            self.assertIn("Huế", fast_reply)

    def test_14_web_search_grounding_citations_http400_fallback_and_tts_stripping(self):
        self.assertTrue(bot._needs_web_search_grounding("Phiên bản SwiftUI hoặc Flutter mới nhất hiện nay có gì mới?"))
        self.assertTrue(bot._needs_web_search_grounding("Giá vàng hôm nay bao nhiêu, tra trên mạng giúp mình"))
        self.assertFalse(bot._needs_web_search_grounding("Dự án của mình đang làm tên gì?"))
        self.assertFalse(bot._needs_web_search_grounding("Thời tiết Đà Nẵng hôm nay"))

        sent_payloads = []
        gemini_grounded_response = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "Swift 6 nâng cấp kiểm tra an toàn đồng thời (data-race safety) ngay lúc biên dịch."}
                        ]
                    },
                    "groundingMetadata": {
                        "groundingChunks": [
                            {"web": {"title": "Swift.org Blog", "uri": "https://www.swift.org/blog/"}},
                            {"web": {"title": "Apple Developer", "uri": "https://developer.apple.com/swift/"}},
                        ]
                    },
                }
            ]
        }

        def _fake_grounded_urlopen(req, timeout=15.0):
            sent_payloads.append(json.loads(req.data.decode("utf-8")))
            return _FakeHttpResponse(gemini_grounded_response)

        with patch.object(bot, "GEMINI_API_KEY", "test_fake_key_not_real"), patch(
            "urllib.request.urlopen", side_effect=_fake_grounded_urlopen
        ):
            reply = bot._ask_gemini(
                "Phiên bản Swift mới nhất hiện nay có gì đáng chú ý?",
                "System prompt test",
                prefs={"style": "balanced", "plain_text": False},
            )

        self.assertTrue(sent_payloads)
        self.assertEqual(sent_payloads[0].get("tools"), [{"google_search": {}}])
        self.assertIn("🌐 Nguồn:", reply)
        self.assertIn("[Swift.org Blog](https://www.swift.org/blog/)", reply)

        # Kiểm tra _prepare_tts_text lọc sạch dòng 🌐 Nguồn: để loa Voice Mode không đọc URL thừa
        tts_cleaned = app_module._prepare_tts_text(reply)
        self.assertNotIn("Nguồn:", tts_cleaned)
        self.assertNotIn("swift.org", tts_cleaned.lower())
        self.assertIn("Swift 6", tts_cleaned)

        # Kiểm tra tự động retry không tool khi model trả HTTP 400 cho google_search
        retry_payloads = []

        def _fake_http400_then_ok(req, timeout=15.0):
            body = json.loads(req.data.decode("utf-8"))
            retry_payloads.append(body)
            if "tools" in body:
                raise urllib.error.HTTPError(
                    req.full_url, 400, "Bad Request: google_search not supported", hdrs=None, fp=io.BytesIO(b"{}")
                )
            return _FakeHttpResponse(
                {"candidates": [{"content": {"parts": [{"text": "Câu trả lời fallback thành công sau HTTP 400."}]}}]}
            )

        with patch.object(bot, "GEMINI_API_KEY", "test_fake_key_not_real"), patch(
            "urllib.request.urlopen", side_effect=_fake_http400_then_ok
        ):
            fallback_reply = bot._ask_gemini(
                "Tin tức công nghệ mới nhất hôm nay",
                "System prompt test",
            )

        self.assertGreaterEqual(len(retry_payloads), 2)
        self.assertIn("tools", retry_payloads[0])
        self.assertNotIn("tools", retry_payloads[1])
        self.assertIn("fallback thành công", fallback_reply)


class TestM4HeadlessBrowserE2E(_M4IsolatedBaseTestCase):
    def test_15_real_headless_edge_m4_memory_center_isolation_and_xss_links(self):
        browser_bin = _find_headless_chromium_binary()
        self.assertIsNotNone(browser_bin, "Microsoft Edge or Chrome is required on Windows for real DOM E2E test")

        report_holder: dict = {}
        report_event = threading.Event()

        e2e_js_runner = r"""
        <script>
        (async function runM4BrowserE2E() {
          const results = { checks: {}, errors: [] };
          const assert = (name, cond, detail = "") => {
            results.checks[name] = Boolean(cond);
            if (!cond) {
              results.errors.push(name + (detail ? ": " + detail : ""));
            }
          };
          const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

          try {
            localStorage.removeItem("wisp_chat_history");
            await fetch("/api/memories?kind=all", { method: "DELETE" });
            await window.clearAllConversationHistory();
            await window.fetchMemoriesState();

            const profileBtn = document.getElementById("userProfileBtn");
            const profilePanel = document.getElementById("userProfilePanel");
            const badgeEl = document.getElementById("memoryCountBadge");
            const tabProfileBtn = document.getElementById("memTabProfileBtn");
            const tabMemoriesBtn = document.getElementById("memTabMemoriesBtn");
            const tabTasksBtn = document.getElementById("memTabTasksBtn");
            const paneProfile = document.getElementById("memPaneProfile");
            const paneMemories = document.getElementById("memPaneMemories");
            const paneTasks = document.getElementById("memPaneTasks");

            assert("m4_tabs_exist", Boolean(tabProfileBtn && tabMemoriesBtn && tabTasksBtn));
            assert("m4_badge_hidden_when_zero", badgeEl && badgeEl.hasAttribute("hidden"));

            // 1. Kể tự nhiên trong khung chat + đặt lịch nhắc việc (với stub stream ổn định cho câu kể)
            await window.sendMessage("Ghi nhớ: Mình đang làm dự án SwiftUI tên FitNote và không uống được cà phê buổi tối");
            await window.sendMessage("Nhắc mình sáng thứ Hai nộp bản build TestFlight");
            await sleep(200);
            await window.fetchMemoriesState();

            assert(
              "m4_badge_shows_3_items",
              badgeEl && !badgeEl.hasAttribute("hidden") && Number(badgeEl.textContent) === 3,
              badgeEl ? badgeEl.textContent : "no_badge"
            );

            // 2. Mở Popover Bộ nhớ -> Tab 1 (Hồ sơ) mặc định hiển thị
            profileBtn.click();
            await sleep(120);
            assert("m4_panel_opens_on_profile_tab", !profilePanel.hasAttribute("hidden") && !paneProfile.hasAttribute("hidden"));

            // 3. Chuyển sang Tab 2 (Ký ức): kiểm tra danh sách, Thêm nhanh, Ghim, Tìm kiếm không dấu, Xóa
            tabMemoriesBtn.click();
            await sleep(180);
            assert(
              "m4_memories_pane_visible",
              !paneMemories.hasAttribute("hidden") && paneProfile.hasAttribute("hidden") && paneTasks.hasAttribute("hidden")
            );
            const memContainer = document.getElementById("memoryListContainer");
            assert(
              "m4_learned_memories_rendered",
              memContainer.textContent.includes("FitNote") && memContainer.textContent.toLowerCase().includes("cà phê"),
              memContainer.textContent
            );

            // Thêm ký ức thủ công từ UI
            const memAddInput = document.getElementById("memoryAddInput");
            const memCatSelect = document.getElementById("memoryCategorySelect");
            memCatSelect.value = "skill";
            memAddInput.value = "Thành thạo Metal Shader cho hiệu ứng iOS";
            document.getElementById("memoryAddBtn").click();
            await sleep(220);
            assert("m4_manual_memory_added", memContainer.textContent.includes("Metal Shader"));

            // Bấm nút Ghim trên thẻ FitNote -> Popover KHÔNG được đóng nhầm khi DOM re-render!
            const cardsBeforePin = Array.from(memContainer.querySelectorAll(".memory-item-card"));
            const fitNoteCard = cardsBeforePin.find((c) => c.textContent.includes("FitNote"));
            const fitNotePinBtn = fitNoteCard ? fitNoteCard.querySelector(".pin-btn") : null;
            assert("m4_fitnote_pin_btn_found", Boolean(fitNotePinBtn));
            if (fitNotePinBtn) {
              fitNotePinBtn.click();
              await sleep(220);
            }
            assert("m4_panel_stays_open_after_pin_click", !profilePanel.hasAttribute("hidden"));
            const fitNoteCardAfter = Array.from(memContainer.querySelectorAll(".memory-item-card")).find((c) =>
              c.textContent.includes("FitNote")
            );
            assert(
              "m4_fitnote_card_marked_pinned",
              Boolean(fitNoteCardAfter && fitNoteCardAfter.classList.contains("pinned"))
            );

            // Tìm kiếm không dấu tiếng Việt ("ca phe")
            const searchInput = document.getElementById("memorySearchInput");
            searchInput.value = "ca phe";
            searchInput.dispatchEvent(new Event("input", { bubbles: true }));
            await sleep(60);
            const visibleDuringSearch = memContainer.querySelectorAll(".memory-item-card");
            assert(
              "m4_unaccented_search_filters_accurately",
              visibleDuringSearch.length === 1 && visibleDuringSearch[0].textContent.toLowerCase().includes("cà phê"),
              String(visibleDuringSearch.length)
            );
            searchInput.value = "";
            searchInput.dispatchEvent(new Event("input", { bubbles: true }));
            await sleep(60);

            // Xóa thẻ Metal Shader vừa thêm thủ công
            const metalCard = Array.from(memContainer.querySelectorAll(".memory-item-card")).find((c) =>
              c.textContent.includes("Metal Shader")
            );
            const metalDelBtn = metalCard ? metalCard.querySelector(".mem-delete-btn") : null;
            if (metalDelBtn) {
              metalDelBtn.click();
              await sleep(220);
            }
            assert("m4_manual_memory_deleted_and_panel_open", !memContainer.textContent.includes("Metal Shader") && !profilePanel.hasAttribute("hidden"));

            // 4. Chuyển sang Tab 3 (Việc cần nhớ): kiểm tra TestFlight, thêm việc mới, tick hoàn thành, dọn việc xong
            tabTasksBtn.click();
            await sleep(180);
            const taskContainer = document.getElementById("taskListContainer");
            assert("m4_tasks_pane_visible", !paneTasks.hasAttribute("hidden") && paneMemories.hasAttribute("hidden"));
            assert(
              "m4_testflight_task_with_due_rendered",
              taskContainer.textContent.includes("TestFlight") && taskContainer.textContent.includes("⏰"),
              taskContainer.textContent
            );

            const taskAddInput = document.getElementById("taskAddInput");
            taskAddInput.value = "Kiểm tra chứng chỉ APNs";
            document.getElementById("taskAddBtn").click();
            await sleep(220);
            assert("m4_manual_task_added", taskContainer.textContent.includes("APNs"));

            const apnsRow = Array.from(taskContainer.querySelectorAll(".task-item-row")).find((r) =>
              r.textContent.includes("APNs")
            );
            const apnsCheck = apnsRow ? apnsRow.querySelector(".task-check-btn") : null;
            if (apnsCheck) {
              apnsCheck.click();
              await sleep(220);
            }
            const apnsRowAfter = Array.from(taskContainer.querySelectorAll(".task-item-row")).find((r) =>
              r.textContent.includes("APNs")
            );
            assert(
              "m4_task_checked_done_and_panel_open",
              Boolean(apnsRowAfter && apnsRowAfter.classList.contains("done") && !profilePanel.hasAttribute("hidden"))
            );

            document.getElementById("clearDoneTasksBtn").click();
            await sleep(240);
            assert(
              "m4_clear_done_keeps_pending_testflight",
              !taskContainer.textContent.includes("APNs") && taskContainer.textContent.includes("TestFlight")
            );

            // Chuyển lại Tab 1 và đóng Popover
            tabProfileBtn.click();
            profileBtn.click();
            assert("m4_panel_closed_cleanly", profilePanel.hasAttribute("hidden"));

            // 5. Bấm nút Xóa lịch sử chat -> Khung chat sạch trắng nhưng Ký ức & Việc cần nhớ CÒN NGUYÊN 100%!
            const chatEl = document.getElementById("chat");
            const voiceChatEl = document.getElementById("voiceChat");
            assert("m4_chat_non_empty_before_clear", chatEl.children.length >= 4);

            document.getElementById("clearChatBtn").click();
            await sleep(280);
            const apiMemAfterClear = await (await fetch("/api/memories")).json();
            assert(
              "m4_clear_chat_empties_bubbles_only",
              chatEl.children.length === 0 && voiceChatEl.children.length === 0
            );
            assert(
              "m4_clear_chat_preserves_100pct_memories_and_tasks",
              apiMemAfterClear.counts.memories_total === 2 &&
                apiMemAfterClear.counts.pinned_total === 1 &&
                apiMemAfterClear.counts.pending_tasks === 1 &&
                !badgeEl.hasAttribute("hidden") &&
                Number(badgeEl.textContent) === 3,
              JSON.stringify(apiMemAfterClear.counts)
            );

            // 6. Kiểm tra render liên kết Markdown Web Grounding & chống XSS tuyệt đối
            const groundingMd =
              "Thông tin mới nhất về **Swift 6**.\n\n🌐 Nguồn: [Swift.org](https://www.swift.org/blog/?v=6&os=win) · [Wiki (Swift)](https://vi.wikipedia.org/wiki/Swift_(lang))";
            const groundingHtml = window.formatSafeMessageHtml(groundingMd);
            const probe = document.createElement("div");
            probe.innerHTML = groundingHtml;
            const anchors = probe.querySelectorAll("a.chat-md-link");
            assert("m4_grounding_renders_two_safe_anchors", anchors.length === 2, groundingHtml);
            assert(
              "m4_grounding_anchor_attrs_noopener_and_query_amp",
              anchors.length === 2 &&
                anchors[0].getAttribute("target") === "_blank" &&
                anchors[0].getAttribute("rel") === "noopener noreferrer" &&
                anchors[0].getAttribute("href") === "https://www.swift.org/blog/?v=6&os=win" &&
                anchors[1].getAttribute("href") === "https://vi.wikipedia.org/wiki/Swift_(lang)"
            );

            const edgeMd =
              "Xem [Moore's Law](https://en.wikipedia.org/wiki/Moore's_law) và [[PDF] Swift Guide](https://swift.org/guide.pdf).";
            const edgeHtml = window.formatSafeMessageHtml(edgeMd);
            const edgeProbe = document.createElement("div");
            edgeProbe.innerHTML = edgeHtml;
            const edgeAnchors = edgeProbe.querySelectorAll("a.chat-md-link");
            assert(
              "m4_apostrophe_url_and_bracketed_label_render_cleanly",
              edgeAnchors.length === 2 &&
                edgeAnchors[0].getAttribute("href") === "https://en.wikipedia.org/wiki/Moore%27s_law" &&
                edgeAnchors[1].getAttribute("href") === "https://swift.org/guide.pdf" &&
                edgeAnchors[1].textContent === "[PDF] Swift Guide" &&
                edgeProbe.textContent.trim().endsWith("."),
              edgeHtml
            );

            const xssMd =
              "[XSS1](javascript:alert(1)) " +
              "[XSS2](JAVASCRIPT:prompt(1)) " +
              "[XSS3](data:text/html,<script>alert(1)<\\/script>) " +
              "[XSS4](https://evil.com\"onmouseover=\"alert(1)) " +
              "[<img src=x onerror=alert(1)>](https://swift.org)";
            const xssHtml = window.formatSafeMessageHtml(xssMd);
            const xssProbe = document.createElement("div");
            xssProbe.innerHTML = xssHtml;
            const xssAnchors = Array.from(xssProbe.querySelectorAll("a"));
            const noInlineEventAttrs = Array.from(xssProbe.querySelectorAll("*")).every(
              (el) => !el.hasAttribute("onmouseover") && !el.hasAttribute("onerror") && !el.hasAttribute("onclick")
            );
            assert(
              "m4_xss_blocks_javascript_data_and_attr_breakout",
              xssAnchors.length === 1 &&
                xssAnchors[0].getAttribute("href") === "https://swift.org" &&
                xssProbe.querySelectorAll("img, script").length === 0 &&
                noInlineEventAttrs,
              xssHtml
            );
          } catch (err) {
            results.errors.push("Unhandled E2E exception: " + (err && err.stack ? err.stack : String(err)));
          }

          await fetch("/__m4_e2e_report__", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(results),
          });
        })();
        </script>
        """

        app_obj = app_module.app

        def _e2e_wsgi_app(environ, start_response):
            path = environ.get("PATH_INFO", "")
            if path == "/__m4_e2e_harness__":
                with app_obj.test_request_context("/"):
                    base_html = app_module.index()
                injected_html = base_html.replace("</body>", e2e_js_runner + "\n</body>")
                body_bytes = injected_html.encode("utf-8")
                start_response(
                    "200 OK",
                    [
                        ("Content-Type", "text/html; charset=utf-8"),
                        ("Content-Length", str(len(body_bytes))),
                    ],
                )
                return [body_bytes]

            if path == "/__m4_e2e_report__" and environ.get("REQUEST_METHOD") == "POST":
                length = int(environ.get("CONTENT_LENGTH") or 0)
                raw_body = environ["wsgi.input"].read(length).decode("utf-8", errors="replace")
                try:
                    report_holder["data"] = json.loads(raw_body)
                except Exception as exc:
                    report_holder["data"] = {"checks": {}, "errors": [f"JSON parse error: {exc}"]}
                report_event.set()
                resp_bytes = b'{"ok":true}'
                start_response(
                    "200 OK",
                    [
                        ("Content-Type", "application/json; charset=utf-8"),
                        ("Content-Length", str(len(resp_bytes))),
                    ],
                )
                return [resp_bytes]

            return app_obj.wsgi_app(environ, start_response)

        server = make_server("127.0.0.1", 0, _e2e_wsgi_app, threaded=True)
        port = server.server_port
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        temp_profile_dir = tempfile.mkdtemp(prefix="wisp_m4_edge_")
        proc = None
        try:
            url = f"http://127.0.0.1:{port}/__m4_e2e_harness__"
            cmd = [
                browser_bin,
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-extensions",
                "--mute-audio",
                "--autoplay-policy=no-user-gesture-required",
                f"--user-data-dir={temp_profile_dir}",
                url,
            ]
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            finished = report_event.wait(timeout=25.0)
            self.assertTrue(finished, "Timed out waiting for Headless Edge M4 E2E report")
        finally:
            if proc is not None:
                try:
                    proc.terminate()
                    proc.wait(timeout=3)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            server.shutdown()
            server.server_close()
            shutil.rmtree(temp_profile_dir, ignore_errors=True)

        report = report_holder.get("data") or {}
        errors = report.get("errors") or []
        checks = report.get("checks") or {}
        self.assertEqual(
            errors,
            [],
            f"M4 Browser E2E checks failed: {errors}\nAll checks: {json.dumps(checks, indent=2)}",
        )
        self.assertGreaterEqual(len(checks), 19, f"Expected >=19 browser assertions, got {len(checks)}")
        for check_name, passed in checks.items():
            self.assertTrue(passed, f"Browser assertion {check_name} was False")


class TestM4AdversarialRegressions(_M4IsolatedBaseTestCase):
    def test_16_adversarial_memory_dedup_distinct_entities_objects_and_subset_preservation(self):
        # 1A. Hai dự án cùng stack SwiftUI/iOS nhưng khác tên thực thể (FitNote vs ExpensePro) -> KHÔNG được ghi đè
        m_fit = memory_store.upsert_memory("Đang làm dự án SwiftUI cho iOS tên FitNote")
        m_exp = memory_store.upsert_memory("Đang làm dự án SwiftUI cho iOS tên ExpensePro")
        self.assertNotEqual(m_fit["id"], m_exp["id"])
        self.assertEqual(len(memory_store.list_memories()), 2)

        # 2. Mở rộng chi tiết của FitNote -> gộp vào đúng bản ghi FitNote; nhắc lại câu ngắn hơn -> bảo toàn chi tiết đầy đủ
        m_fit_rich = memory_store.upsert_memory(
            "Đang làm dự án SwiftUI cho iOS tên FitNote tích hợp HealthKit và CloudKit"
        )
        self.assertEqual(m_fit_rich["id"], m_fit["id"])
        m_fit_short = memory_store.upsert_memory("Đang làm dự án SwiftUI tên FitNote", source="fast_rule")
        self.assertEqual(m_fit_short["id"], m_fit["id"])
        self.assertIn("HealthKit", m_fit_short["content"])
        self.assertIn("CloudKit", m_fit_short["content"])

        # 1B. Hai món ăn sáng khác đối tượng chính (phở bò tái vs bún bò tái) -> tách riêng 2 bản ghi
        m_pho = memory_store.upsert_memory("Thích ăn phở bò tái vào buổi sáng")
        m_bun = memory_store.upsert_memory("Thích ăn bún bò tái vào buổi sáng")
        self.assertNotEqual(m_pho["id"], m_bun["id"])

        # 3A. Quên ký ức bằng câu tự nhiên "quên chuyện mình dị ứng hải sản đi"
        m_allergy = memory_store.upsert_memory("Dị ứng với hải sản")
        deleted_allergy = memory_store.forget_memories_by_query("quên chuyện mình dị ứng hải sản đi")
        self.assertEqual([d["id"] for d in deleted_allergy], [m_allergy["id"]])

        # 3B. Quên dự án cụ thể "dự án FitNote SwiftUI iOS" -> chỉ xóa FitNote, BẢO TOÀN kỹ năng SwiftUI/iOS chung và ExpensePro
        m_skill = memory_store.upsert_memory("Có 4 năm kinh nghiệm lập trình SwiftUI trên iOS", category="skill")
        deleted_proj = memory_store.forget_memories_by_query("dự án FitNote SwiftUI iOS")
        self.assertEqual([d["id"] for d in deleted_proj], [m_fit["id"]])
        surviving_ids = {m["id"] for m in memory_store.list_memories()}
        self.assertIn(m_skill["id"], surviving_ids)
        self.assertIn(m_exp["id"], surviving_ids)

    def test_17_adversarial_due_time_fraction_immunity_and_midnight_rollover(self):
        base_now = datetime(2026, 10, 31, 10, 0, tzinfo=memory_store.VN_TZ)

        # Phân số / số chương không được cướp quyền ưu tiên của "tối nay"
        _, iso_chap = memory_store.parse_vietnamese_due_time("đọc chương 3/4 tối nay", now_dt=base_now)
        self.assertEqual(iso_chap, "2026-10-31T20:00")

        # Phân số không có ngày -> không bị ép thành ngày 01/02
        txt_frac, iso_frac = memory_store.parse_vietnamese_due_time("làm bài 1/2 trang 5", now_dt=base_now)
        self.assertEqual((txt_frac, iso_frac), ("", ""))

        # "12h đêm nay" nói lúc 10:00 sáng -> 00:00 rạng sáng hôm sau (không phải 12:00 trưa hay 00:00 quá khứ)
        _, iso_mid = memory_store.parse_vietnamese_due_time("12h đêm nay tắt máy server", now_dt=base_now)
        self.assertEqual(iso_mid, "2026-11-01T00:00")

    def test_18_adversarial_tier1_operator_symbols_inline_backticks_and_unaccented_vietnamese(self):
        base_now = datetime(2026, 10, 31, 10, 0, tzinfo=memory_store.VN_TZ)

        ev_arrow = bot.extract_instant_memory_and_tasks("Ghi nhớ: Mình chuyển từ UIKit -> SwiftUI", now_dt=base_now)
        self.assertEqual(len(ev_arrow["memories_added"]), 1)
        self.assertIn("UIKit -> SwiftUI", ev_arrow["memories_added"][0]["content"])

        ev_eq = bot.extract_instant_memory_and_tasks("Ghi nhớ: Mình đặt mục tiêu BMI = 22", now_dt=base_now)
        self.assertEqual(len(ev_eq["memories_added"]), 1)
        self.assertIn("BMI = 22", ev_eq["memories_added"][0]["content"])

        ev_ge = bot.extract_instant_memory_and_tasks(
            "Nhắc mình chiều nay nâng cấp Python >= 3.11 và chạy `pytest`",
            now_dt=base_now,
        )
        self.assertEqual(len(ev_ge["tasks_added"]), 1)
        self.assertIn("Python >= 3.11", ev_ge["tasks_added"][0]["title"])
        self.assertIn("pytest", ev_ge["tasks_added"][0]["title"])
        self.assertEqual(ev_ge["tasks_added"][0]["due_iso"], "2026-10-31T15:00")

        # Gõ hoàn toàn không dấu tiếng Việt
        ev_unacc_task = bot.extract_instant_memory_and_tasks(
            "nhac minh sang thu hai nop ban build testflight",
            now_dt=base_now,
        )
        self.assertEqual(len(ev_unacc_task["tasks_added"]), 1)
        self.assertEqual(ev_unacc_task["tasks_added"][0]["title"], "Nop ban build testflight")
        self.assertEqual(ev_unacc_task["tasks_added"][0]["due_iso"], "2026-11-02T08:00")

        ev_unacc_mem = bot.extract_instant_memory_and_tasks(
            "minh dang lam du an SwiftUI ten ExpenseTracker",
            now_dt=base_now,
        )
        self.assertEqual(len(ev_unacc_mem["memories_added"]), 1)
        self.assertIn("ExpenseTracker", ev_unacc_mem["memories_added"][0]["content"])

        # Miễn nhiễm ngôi thứ 3 & câu hỏi khi gõ không dấu
        cnt_before = memory_store.get_counts()["memories_total"]
        bot.extract_instant_memory_and_tasks("ban than cua minh thich an sau rieng")
        bot.extract_instant_memory_and_tasks("minh dang lam du an ten gi")
        self.assertEqual(memory_store.get_counts()["memories_total"], cnt_before)

    def test_19_adversarial_gemini_thinking_config_http400_fallback_and_concurrent_tier1_tier2(self):
        # 1. Model từ chối hoàn toàn thinkingConfig (kể cả thinkingBudget=0) với HTTP 400 -> tự động gỡ thinkingConfig và thành công
        seen_payloads: list[dict] = []

        def _reject_thinking_urlopen(req, timeout=5.0):
            body = json.loads(req.data.decode("utf-8"))
            seen_payloads.append(body)
            gen_cfg = body.get("generationConfig") or {}
            if "thinkingConfig" in gen_cfg:
                raise urllib.error.HTTPError(req.full_url, 400, "thinkingConfig unsupported", hdrs=None, fp=None)
            return _FakeHttpResponse(
                {"candidates": [{"content": {"parts": [{"text": "Phản hồi thành công sau khi gỡ thinkingConfig."}]}}]}
            )

        with patch.object(bot, "GEMINI_API_KEY", "fake-key"), patch(
            "urllib.request.urlopen", side_effect=_reject_thinking_urlopen
        ):
            reply = bot._ask_gemini("Chào bạn, giải thích ngắn gọn giúp mình", "SYS")
        self.assertIsNotNone(reply)
        self.assertIn("thành công", reply)
        self.assertIn("thinkingConfig", seen_payloads[0]["generationConfig"])
        self.assertNotIn("thinkingConfig", seen_payloads[-1]["generationConfig"])

        # Tương tự cho luồng SSE streaming (_stream_gemini_tokens)
        sse_payloads: list[dict] = []

        def _reject_thinking_sse(req, timeout=5.0):
            body = json.loads(req.data.decode("utf-8"))
            sse_payloads.append(body)
            if "thinkingConfig" in (body.get("generationConfig") or {}):
                raise urllib.error.HTTPError(req.full_url, 400, "thinkingConfig unsupported", hdrs=None, fp=None)
            sse_line = "data: " + json.dumps(
                {"candidates": [{"content": {"parts": [{"text": "Stream OK không lỗi 400."}]}}]},
                ensure_ascii=False,
            ) + "\n"
            return _FakeHttpResponse(sse_line)

        with patch.object(bot, "GEMINI_API_KEY", "fake-key"), patch(
            "urllib.request.urlopen", side_effect=_reject_thinking_sse
        ):
            chunks = list(bot._stream_gemini_tokens("Câu hỏi ngắn", "SYS"))
        self.assertIn("Stream OK", "".join(chunks))
        self.assertNotIn("thinkingConfig", sse_payloads[-1]["generationConfig"])

        # 2. Chạy song song đa luồng Tầng 1 + Tầng 2 trên cùng một sự thật -> RLock + Dedup đảm bảo đúng 1 bản ghi duy nhất
        errors: list[Exception] = []
        barrier = threading.Barrier(2)

        def _worker_tier1():
            try:
                barrier.wait(timeout=2.0)
                bot.extract_instant_memory_and_tasks("Mình không uống được cà phê buổi tối")
            except Exception as exc:
                errors.append(exc)

        def _worker_tier2():
            try:
                barrier.wait(timeout=2.0)
                with patch.object(
                    bot,
                    "_call_llm_json",
                    return_value={
                        "ops": [
                            {
                                "op": "add_memory",
                                "category": "health",
                                "content": "Không uống được cà phê vào buổi tối vì dễ mất ngủ",
                                "importance": 4,
                            }
                        ]
                    },
                ):
                    bot._run_background_memory_learning_sync("Mình không uống được cà phê buổi tối vì dễ mất ngủ")
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=_worker_tier1)
        t2 = threading.Thread(target=_worker_tier2)
        t1.start()
        t2.start()
        t1.join(timeout=5.0)
        t2.join(timeout=5.0)
        self.assertEqual(errors, [])
        coffee_mems = memory_store.list_memories(query="ca phe")
        self.assertEqual(len(coffee_mems), 1)
        self.assertIn("mất ngủ", coffee_mems[0]["content"])

    def test_20_adversarial_api_memories_validation_returns_400_not_500(self):
        client = app_module.app.test_client()

        bad_imp = client.post("/api/memories", json={"kind": "memory", "content": "Ký ức hợp lệ", "importance": "high"})
        self.assertEqual(bad_imp.status_code, 400)
        self.assertNotIn("Traceback", bad_imp.get_data(as_text=True))

        bad_prio = client.post("/api/memories", json={"kind": "task", "title": "Việc hợp lệ", "priority": "urgent"})
        self.assertEqual(bad_prio.status_code, 400)

        created = client.post("/api/memories", json={"kind": "memory", "content": "Ký ức gốc để sửa"}).get_json()["item"]
        empty_patch = client.patch(f"/api/memories/memory/{created['id']}", json={"content": " "})
        self.assertEqual(empty_patch.status_code, 400)
        bad_imp_patch = client.patch(f"/api/memories/memory/{created['id']}", json={"importance": "invalid"})
        self.assertEqual(bad_imp_patch.status_code, 400)

    def test_21_web_search_http429_403_degrades_to_same_gemini_model_without_falling_to_ollama(self):
        # 1. Kiểm tra chuỗi attempt & timeout: non-streaming = 9.0s, SSE streaming = 5.0s, web/thinking > 0 = 15.0s
        bot._clear_chat_history()
        self.assertEqual(bot._GEMINI_WEB_SEARCH_COOLDOWN_UNTIL, 0.0)

        chain_non_stream = bot._build_gemini_attempt_chain(
            "Câu hỏi ngắn", "SYS", bot.DEFAULT_AI_PREFS, budget=0, enable_web=False, is_streaming=False
        )
        self.assertEqual([ (t, w) for _, t, w in chain_non_stream ], [(9.0, False), (9.0, False)])

        chain_stream = bot._build_gemini_attempt_chain(
            "Câu hỏi ngắn", "SYS", bot.DEFAULT_AI_PREFS, budget=0, enable_web=False, is_streaming=True
        )
        self.assertEqual([ (t, w) for _, t, w in chain_stream ], [(5.0, False), (5.0, False)])

        chain_web_b0 = bot._build_gemini_attempt_chain(
            "Phiên bản Flutter mới nhất hiện nay là bao nhiêu?",
            "SYS",
            bot.DEFAULT_AI_PREFS,
            budget=0,
            enable_web=True,
            is_streaming=False,
        )
        self.assertEqual([ (t, w) for _, t, w in chain_web_b0 ], [(15.0, True), (9.0, False), (9.0, False)])
        b0_p0 = json.loads(chain_web_b0[0][0].decode("utf-8"))
        b0_p1 = json.loads(chain_web_b0[1][0].decode("utf-8"))
        b0_p2 = json.loads(chain_web_b0[2][0].decode("utf-8"))
        self.assertEqual(b0_p0.get("tools"), [{"google_search": {}}])
        self.assertNotIn("tools", b0_p1)
        self.assertEqual(b0_p1["generationConfig"].get("thinkingConfig"), {"thinkingBudget": 0})
        self.assertNotIn("tools", b0_p2)
        self.assertNotIn("thinkingConfig", b0_p2["generationConfig"])

        # 2. Luồng non-streaming (get_reply -> _ask_gemini) khớp 100% thực tế Google AI Studio Free Tier:
        #    gemini-3.5-flash trả HTTP 429 cho MỌI request (0 quota), còn gemini-3.1-flash-lite trả HTTP 429
        #    RIÊNG khi có tools=[google_search] nhưng trả 200 OK khi không có tools -> phải đặt cooldown riêng
        #    cho gemini-3.5-flash, retry thành công ngay trên CÙNG gemini-3.1-flash-lite, bật
        #    _GEMINI_WEB_SEARCH_COOLDOWN_UNTIL (+180s), KHÔNG phạt gemini-3.1-flash-lite và KHÔNG rơi về Ollama!
        non_stream_calls: list[tuple[str, bool, float]] = []

        def _free_tier_non_stream_urlopen(req, timeout=None):
            url = req.full_url
            if "11434" in url:
                raise AssertionError("MUST NOT fall back to local Ollama when Gemini model itself is healthy!")
            body = json.loads(req.data.decode("utf-8"))
            has_tools = "tools" in body
            non_stream_calls.append((url, has_tools, float(timeout or 0.0)))
            if "gemini-3.5-flash:" in url:
                raise urllib.error.HTTPError(url, 429, "Zero free-tier quota on 3.5-flash", hdrs=None, fp=io.BytesIO(b"{}"))
            if has_tools:
                raise urllib.error.HTTPError(
                    url, 429, "You exceeded your current quota for google_search", hdrs=None, fp=io.BytesIO(b"{}")
                )
            return _FakeHttpResponse(
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {"text": "Phản hồi trực tiếp từ Gemini 3.1 Flash-Lite sau khi hạ cấp google_search."}
                                ]
                            }
                        }
                    ]
                }
            )

        with patch.object(bot, "GEMINI_API_KEY", "fake-free-tier-key"), patch.object(
            bot, "_ask_ollama", side_effect=AssertionError("Ollama fallback should NOT be called")
        ) as mock_ollama, patch("urllib.request.urlopen", side_effect=_free_tier_non_stream_urlopen):
            # 2A. Câu hỏi web budget == 0 ("Tin tức công nghệ mới nhất hôm nay"): (True, 15.0) -> 429 -> (False, 9.0) -> 200 OK
            t_before = time.monotonic()
            reply_b0 = bot.get_reply("Tin tức công nghệ mới nhất hôm nay")
            self.assertIn("Gemini 3.1 Flash-Lite", reply_b0)
            mock_ollama.assert_not_called()

            # gemini-3.5-flash hết sạch quota nên bị cooldown model; gemini-3.1-flash-lite khỏe mạnh KHÔNG bị cooldown; google_search bị cooldown 180s
            self.assertIn("gemini-3.5-flash", bot._GEMINI_COOLDOWN_UNTIL)
            self.assertNotIn("gemini-3.1-flash-lite", bot._GEMINI_COOLDOWN_UNTIL)
            self.assertGreaterEqual(bot._GEMINI_WEB_SEARCH_COOLDOWN_UNTIL, t_before + 170.0)

            flash_lite_calls_b0 = [c for c in non_stream_calls if "gemini-3.1-flash-lite:" in c[0]]
            self.assertEqual(len(flash_lite_calls_b0), 2)
            self.assertEqual((flash_lite_calls_b0[0][1], flash_lite_calls_b0[0][2]), (True, 15.0))
            self.assertEqual((flash_lite_calls_b0[1][1], flash_lite_calls_b0[1][2]), (False, 9.0))

            # 2B. Câu hỏi web tiếp theo trong vòng 3 phút ("Phiên bản Flutter mới nhất hiện nay là bao nhiêu?", budget=2048)
            #     phải BỎ QUA luôn bước google_search (0 lần gọi 429 thừa) và trả lời trực tiếp từ Gemini!
            non_stream_calls.clear()
            reply_flutter = bot.get_reply("Phiên bản Flutter mới nhất hiện nay là bao nhiêu?")
            self.assertIn("Gemini 3.1 Flash-Lite", reply_flutter)
            self.assertEqual(len(non_stream_calls), 1)
            self.assertIn("gemini-3.1-flash-lite:", non_stream_calls[0][0])
            self.assertEqual((non_stream_calls[0][1], non_stream_calls[0][2]), (False, 15.0))

            # 2C. Reset cooldown và kiểm tra trực tiếp câu hỏi Flutter (budget=2048) khi chưa có cooldown:
            #     (True, 15.0) -> 429 -> (False, 15.0) -> 200 OK ngay trên cùng gemini-3.1-flash-lite
            bot._GEMINI_WEB_SEARCH_COOLDOWN_UNTIL = 0.0
            non_stream_calls.clear()
            reply_flutter_fresh = bot.get_reply("Phiên bản Flutter mới nhất hiện nay là bao nhiêu?")
            self.assertIn("Gemini 3.1 Flash-Lite", reply_flutter_fresh)
            self.assertEqual([(c[1], c[2]) for c in non_stream_calls], [(True, 15.0), (False, 15.0)])

        # 3A. Luồng SSE streaming (stream_reply -> _stream_gemini_tokens) trên chuỗi GEMINI_MODELS thực tế khi
        #     gemini-3.5-flash hết sạch quota (429 mọi request) và gemini-3.1-flash-lite trả HTTP 429 riêng cho google_search,
        #     đồng thời kiểm tra mọi HTTPError.fp đều được close() ngay lập tức (không rò rỉ socket SSL trong suốt quá trình stream)
        bot._clear_chat_history()
        self.assertEqual(bot._GEMINI_WEB_SEARCH_COOLDOWN_UNTIL, 0.0)

        sse_429_calls: list[tuple[str, bool, float]] = []
        tracked_error_fps: list[io.BytesIO] = []

        def _free_tier_sse_429_urlopen(req, timeout=None):
            url = req.full_url
            if "11434" in url:
                raise AssertionError("stream_reply MUST NOT fall back to local Ollama on tool-only 429!")
            body = json.loads(req.data.decode("utf-8"))
            has_tools = "tools" in body
            sse_429_calls.append((url, has_tools, float(timeout or 0.0)))
            if "gemini-3.5-flash:" in url:
                err_fp = io.BytesIO(b"{}")
                tracked_error_fps.append(err_fp)
                raise urllib.error.HTTPError(url, 429, "Zero free-tier quota on 3.5-flash", hdrs=None, fp=err_fp)
            if has_tools:
                err_fp = io.BytesIO(b"{}")
                tracked_error_fps.append(err_fp)
                raise urllib.error.HTTPError(url, 429, "Search Grounding quota 0 on free tier", hdrs=None, fp=err_fp)

            def _sse_lines():
                # Ngay khi bắt đầu đọc chunk đầu tiên của luồng SSE 200 OK, toàn bộ socket lỗi 429 trước đó phải đã đóng sạch!
                assert all(fp.closed for fp in tracked_error_fps), "Unclosed HTTPError fp leaked during active SSE stream!"
                yield (
                    "data: "
                    + json.dumps(
                        {
                            "candidates": [
                                {
                                    "content": {
                                        "parts": [{"text": "Luồng SSE vượt lỗi 429 google_search trên cùng Flash-Lite thành công."}]
                                    }
                                }
                            ]
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                ).encode("utf-8")

            return _FakeHttpResponse(b"", lines=_sse_lines())

        with patch.object(bot, "GEMINI_API_KEY", "fake-free-tier-key"), patch(
            "urllib.request.urlopen", side_effect=_free_tier_sse_429_urlopen
        ):
            t_sse429_before = time.monotonic()
            events_429 = list(bot.stream_reply("Tin tức công nghệ mới nhất hôm nay"))
            tokens_429 = "".join(e.get("delta", "") for e in events_429 if e.get("type") == "token")
            self.assertIn("vượt lỗi 429 google_search", tokens_429)
            self.assertIn("gemini-3.5-flash", bot._GEMINI_COOLDOWN_UNTIL)
            self.assertNotIn("gemini-3.1-flash-lite", bot._GEMINI_COOLDOWN_UNTIL)
            self.assertGreaterEqual(bot._GEMINI_WEB_SEARCH_COOLDOWN_UNTIL, t_sse429_before + 170.0)
            self.assertEqual(len(tracked_error_fps), 3)
            self.assertTrue(all(fp.closed for fp in tracked_error_fps))
            flash_lite_sse_429 = [(c[1], c[2]) for c in sse_429_calls if "gemini-3.1-flash-lite:" in c[0]]
            self.assertEqual(flash_lite_sse_429, [(True, 15.0), (False, 5.0)])

        # 3B. Kiểm tra _clear_chat_history() reset sạch _GEMINI_WEB_SEARCH_COOLDOWN_UNTIL,
        #     và kiểm tra đối xứng HTTP 403 riêng cho google_search trên cả _ask_gemini lẫn stream_reply
        bot._clear_chat_history()
        self.assertEqual(bot._GEMINI_WEB_SEARCH_COOLDOWN_UNTIL, 0.0)

        def _tool_only_403_non_stream(req, timeout=None):
            body = json.loads(req.data.decode("utf-8"))
            if "tools" in body:
                err_fp = io.BytesIO(b"{}")
                tracked_error_fps.append(err_fp)
                raise urllib.error.HTTPError(req.full_url, 403, "Search Grounding forbidden", hdrs=None, fp=err_fp)
            return _FakeHttpResponse(
                {"candidates": [{"content": {"parts": [{"text": "Hạ cấp 403 non-stream thành công trên cùng model."}]}}]}
            )

        with patch.object(bot, "GEMINI_API_KEY", "fake-free-tier-key"), patch.object(
            bot, "GEMINI_MODELS", ("gemini-3.1-flash-lite",)
        ), patch("urllib.request.urlopen", side_effect=_tool_only_403_non_stream):
            res_403_ns = bot._ask_gemini("Tin tức công nghệ mới nhất hôm nay", "SYS")
            self.assertIn("Hạ cấp 403 non-stream thành công", res_403_ns)
            self.assertGreater(bot._GEMINI_WEB_SEARCH_COOLDOWN_UNTIL, time.monotonic())
            self.assertNotIn("gemini-3.1-flash-lite", bot._GEMINI_COOLDOWN_UNTIL)
            self.assertTrue(tracked_error_fps[-1].closed)

        bot._clear_chat_history()
        sse_calls: list[tuple[str, bool, float]] = []

        def _free_tier_sse_urlopen(req, timeout=None):
            url = req.full_url
            if "11434" in url:
                raise AssertionError("stream_reply MUST NOT fall back to local Ollama on tool-only 403/429!")
            body = json.loads(req.data.decode("utf-8"))
            has_tools = "tools" in body
            sse_calls.append((url, has_tools, float(timeout or 0.0)))
            if has_tools:
                raise urllib.error.HTTPError(
                    url, 403, "Search Grounding permission denied on free tier", hdrs=None, fp=io.BytesIO(b"{}")
                )
            sse_payload = (
                "data: "
                + json.dumps(
                    {
                        "candidates": [
                            {
                                "content": {
                                    "parts": [{"text": "Luồng SSE từ cùng Gemini model thành công không cần Ollama."}]
                                }
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            return _FakeHttpResponse(sse_payload)

        with patch.object(bot, "GEMINI_API_KEY", "fake-free-tier-key"), patch.object(
            bot, "GEMINI_MODELS", ("gemini-3.1-flash-lite",)
        ), patch("urllib.request.urlopen", side_effect=_free_tier_sse_urlopen):
            t_sse_before = time.monotonic()
            events1 = list(bot.stream_reply("Tin tức công nghệ mới nhất hôm nay"))
            tokens1 = "".join(e.get("delta", "") for e in events1 if e.get("type") == "token")
            self.assertIn("Luồng SSE từ cùng Gemini model thành công", tokens1)
            self.assertNotIn("gemini-3.1-flash-lite", bot._GEMINI_COOLDOWN_UNTIL)
            self.assertGreaterEqual(bot._GEMINI_WEB_SEARCH_COOLDOWN_UNTIL, t_sse_before + 170.0)
            self.assertEqual([(c[1], c[2]) for c in sse_calls], [(True, 15.0), (False, 5.0)])

            # Lượt stream thứ 2 trong thời gian cooldown bỏ qua google_search ngay từ đầu và không phát trạng thái searching giả
            sse_calls.clear()
            events2 = list(bot.stream_reply("Phiên bản Flutter mới nhất hiện nay là bao nhiêu?"))
            tokens2 = "".join(e.get("delta", "") for e in events2 if e.get("type") == "token")
            self.assertIn("Luồng SSE từ cùng Gemini model thành công", tokens2)
            self.assertEqual([(c[1], c[2]) for c in sse_calls], [(False, 15.0)])
            searching_labels = [
                e.get("label", "") for e in events2 if e.get("type") == "state" and e.get("state") == "searching"
            ]
            self.assertEqual(searching_labels, [])

        # 4. Ngược lại: Nếu CẢ has_web_tool=True LẪN has_web_tool=False đều trả HTTP 429 (model thật sự hết sạch quota),
        #    phải phạt _GEMINI_COOLDOWN_UNTIL[model_name] và KHÔNG đổ lỗi oan cho _GEMINI_WEB_SEARCH_COOLDOWN_UNTIL!
        bot._clear_chat_history()

        def _always_429_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 429, "Model RPM exhausted", hdrs=None, fp=io.BytesIO(b"{}"))

        with patch.object(bot, "GEMINI_API_KEY", "fake-free-tier-key"), patch.object(
            bot, "GEMINI_MODELS", ("gemini-3.1-flash-lite",)
        ), patch("urllib.request.urlopen", side_effect=_always_429_urlopen):
            res_none = bot._ask_gemini("Phiên bản Flutter mới nhất hiện nay là bao nhiêu?", "SYS")
            self.assertIsNone(res_none)
            self.assertGreater(bot._GEMINI_COOLDOWN_UNTIL.get("gemini-3.1-flash-lite", 0.0), time.monotonic())
            self.assertEqual(bot._GEMINI_WEB_SEARCH_COOLDOWN_UNTIL, 0.0)

        bot._clear_chat_history()
        with patch.object(bot, "GEMINI_API_KEY", "fake-free-tier-key"), patch.object(
            bot, "GEMINI_MODELS", ("gemini-3.1-flash-lite",)
        ), patch("urllib.request.urlopen", side_effect=_always_429_urlopen):
            with self.assertRaises(RuntimeError):
                list(bot._stream_gemini_tokens("Phiên bản Flutter mới nhất hiện nay là bao nhiêu?", "SYS"))
            self.assertGreater(bot._GEMINI_COOLDOWN_UNTIL.get("gemini-3.1-flash-lite", 0.0), time.monotonic())
            self.assertEqual(bot._GEMINI_WEB_SEARCH_COOLDOWN_UNTIL, 0.0)

        # 5. Edge case: Khóa API bị thu hồi (HTTP 403 cho CẢ web=True LẪN web=False) -> hủy toàn bộ các model sau ngay lập tức
        #    trên cả _ask_gemini và _stream_gemini_tokens, không bật web cooldown
        bot._clear_chat_history()
        seen_403_models: list[str] = []

        def _always_403_urlopen(req, timeout=None):
            seen_403_models.append(req.full_url)
            raise urllib.error.HTTPError(req.full_url, 403, "API key invalid", hdrs=None, fp=io.BytesIO(b"{}"))

        with patch.object(bot, "GEMINI_API_KEY", "revoked-key"), patch.object(
            bot, "GEMINI_MODELS", ("gemini-3.5-flash", "gemini-3.1-flash-lite")
        ), patch("urllib.request.urlopen", side_effect=_always_403_urlopen):
            self.assertIsNone(bot._ask_gemini("Tin tức công nghệ mới nhất hôm nay", "SYS"))
            self.assertEqual(len(seen_403_models), 2)
            self.assertTrue(all("gemini-3.5-flash:" in u for u in seen_403_models))
            self.assertEqual(bot._GEMINI_WEB_SEARCH_COOLDOWN_UNTIL, 0.0)

            seen_403_models.clear()
            with self.assertRaises(RuntimeError):
                list(bot._stream_gemini_tokens("Tin tức công nghệ mới nhất hôm nay", "SYS"))
            self.assertEqual(len(seen_403_models), 2)
            self.assertTrue(all("gemini-3.5-flash:" in u for u in seen_403_models))
            self.assertEqual(bot._GEMINI_WEB_SEARCH_COOLDOWN_UNTIL, 0.0)

        # 6. Edge case kết hợp: Attempt 0 (web=True) -> 429, Attempt 1 (web=False, thinkingConfig) -> 400, Attempt 2 (minimal) -> 200 OK
        #    trên cả _ask_gemini lẫn _stream_gemini_tokens
        bot._clear_chat_history()

        def _chain_429_400_200_urlopen(req, timeout=None):
            body = json.loads(req.data.decode("utf-8"))
            if "tools" in body:
                raise urllib.error.HTTPError(req.full_url, 429, "Search quota 0", hdrs=None, fp=io.BytesIO(b"{}"))
            if "thinkingConfig" in (body.get("generationConfig") or {}):
                raise urllib.error.HTTPError(req.full_url, 400, "thinkingConfig unsupported", hdrs=None, fp=io.BytesIO(b"{}"))
            if "streamGenerateContent" in req.full_url:
                return _FakeHttpResponse(
                    'data: {"candidates":[{"content":{"parts":[{"text":"SSE hạ cấp 2 bậc 429 -> 400 -> 200 thành công."}]}}]}\n'
                )
            return _FakeHttpResponse(
                {"candidates": [{"content": {"parts": [{"text": "Hạ cấp 2 bậc 429 -> 400 -> 200 thành công."}]}}]}
            )

        with patch.object(bot, "GEMINI_API_KEY", "fake-free-tier-key"), patch.object(
            bot, "GEMINI_MODELS", ("gemini-3.1-flash-lite",)
        ), patch("urllib.request.urlopen", side_effect=_chain_429_400_200_urlopen):
            res_chain = bot._ask_gemini("Tin tức công nghệ mới nhất hôm nay", "SYS")
            self.assertIn("429 -> 400 -> 200 thành công", res_chain)
            self.assertGreater(bot._GEMINI_WEB_SEARCH_COOLDOWN_UNTIL, time.monotonic())
            self.assertNotIn("gemini-3.1-flash-lite", bot._GEMINI_COOLDOWN_UNTIL)

            bot._GEMINI_WEB_SEARCH_COOLDOWN_UNTIL = 0.0
            sse_chain = "".join(bot._stream_gemini_tokens("Tin tức công nghệ mới nhất hôm nay", "SYS"))
            self.assertIn("SSE hạ cấp 2 bậc 429 -> 400 -> 200 thành công", sse_chain)
            self.assertGreater(bot._GEMINI_WEB_SEARCH_COOLDOWN_UNTIL, time.monotonic())
            self.assertNotIn("gemini-3.1-flash-lite", bot._GEMINI_COOLDOWN_UNTIL)

        # 7. Đa luồng & cập nhật cooldown giữa chừng (mid-flight cooldown):
        #    - Khi luồng khác vừa bật _GEMINI_WEB_SEARCH_COOLDOWN_UNTIL trong lúc model 1 đang lỗi 503, model 2 phải bỏ qua ngay bước has_web_tool=True!
        #    - 8 luồng đồng thời cùng gặp 429 google_search đều thành công trên Gemini mà không lỗi race condition hay phạt nhầm model.
        bot._clear_chat_history()
        midflight_calls: list[tuple[str, bool]] = []

        def _midflight_race_urlopen(req, timeout=None):
            body = json.loads(req.data.decode("utf-8"))
            has_tools = "tools" in body
            model_id = req.full_url.split("/models/")[1].split(":")[0]
            midflight_calls.append((model_id, has_tools))
            if model_id == "gemini-3.5-flash":
                bot._GEMINI_WEB_SEARCH_COOLDOWN_UNTIL = time.monotonic() + 180.0
                raise urllib.error.HTTPError(req.full_url, 503, "High demand", hdrs=None, fp=io.BytesIO(b"{}"))
            if has_tools:
                raise AssertionError("Model 2 MUST skip has_web_tool=True once _GEMINI_WEB_SEARCH_COOLDOWN_UNTIL is active!")
            return _FakeHttpResponse({"candidates": [{"content": {"parts": [{"text": "Mid-flight skip OK."}]}}]})

        with patch.object(bot, "GEMINI_API_KEY", "fake-free-tier-key"), patch(
            "urllib.request.urlopen", side_effect=_midflight_race_urlopen
        ):
            self.assertIn("Mid-flight skip OK", bot._ask_gemini("Tin tức công nghệ mới nhất hôm nay", "SYS"))
            self.assertEqual(midflight_calls, [("gemini-3.5-flash", True), ("gemini-3.1-flash-lite", False)])

        bot._clear_chat_history()
        race_barrier = threading.Barrier(8)
        race_errors: list[Exception] = []
        race_replies: list[str] = []
        race_lock = threading.Lock()

        def _concurrent_429_urlopen(req, timeout=None):
            body = json.loads(req.data.decode("utf-8"))
            if "tools" in body:
                raise urllib.error.HTTPError(req.full_url, 429, "Search quota 0", hdrs=None, fp=io.BytesIO(b"{}"))
            if "streamGenerateContent" in req.full_url:
                return _FakeHttpResponse(
                    'data: {"candidates":[{"content":{"parts":[{"text":"Concurrent SSE Gemini OK."}]}}]}\n'
                )
            return _FakeHttpResponse({"candidates": [{"content": {"parts": [{"text": "Concurrent Gemini OK."}]}}]})

        def _race_worker(idx: int):
            try:
                race_barrier.wait(timeout=3.0)
                if idx % 2 == 0:
                    out = bot._ask_gemini("Tin tức công nghệ mới nhất hôm nay", "SYS") or ""
                else:
                    out = "".join(bot._stream_gemini_tokens("Tin tức công nghệ mới nhất hôm nay", "SYS"))
                with race_lock:
                    race_replies.append(out)
            except Exception as exc:
                with race_lock:
                    race_errors.append(exc)

        with patch.object(bot, "GEMINI_API_KEY", "fake-free-tier-key"), patch.object(
            bot, "GEMINI_MODELS", ("gemini-3.1-flash-lite",)
        ), patch("urllib.request.urlopen", side_effect=_concurrent_429_urlopen):
            threads = [threading.Thread(target=_race_worker, args=(i,)) for i in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5.0)
            self.assertEqual(race_errors, [])
            self.assertEqual(len(race_replies), 8)
            self.assertTrue(all("Gemini OK" in r for r in race_replies))
            self.assertGreater(bot._GEMINI_WEB_SEARCH_COOLDOWN_UNTIL, time.monotonic())
            self.assertNotIn("gemini-3.1-flash-lite", bot._GEMINI_COOLDOWN_UNTIL)

        # 8. Edge case đứt dòng SSE giữa chừng (partial mid-stream error) & ngắt mạch offline circuit-breaker:
        #    - Sau 429 ở web=True, nếu web=False trả 200 OK và đã stream nội dung thật rồi mới gặp JSON lỗi ở dòng cuối,
        #      vẫn phải bảo toàn câu trả lời một phần, bật _GEMINI_WEB_SEARCH_COOLDOWN_UNTIL và không phạt model.
        #    - Nếu chunk lỗi chỉ toàn khoảng trắng rỗng thì KHÔNG được ghi assistant rỗng làm hỏng thứ tự role user/model.
        #    - Khi _GEMINI_NETWORK_OFFLINE_UNTIL đang bật, stream_reply không được phát trạng thái searching giả.
        bot._clear_chat_history()

        def _partial_sse_after_429(req, timeout=None):
            body = json.loads(req.data.decode("utf-8"))
            if "tools" in body:
                raise urllib.error.HTTPError(req.full_url, 429, "Search quota 0", hdrs=None, fp=io.BytesIO(b"{}"))

            def _partial_gen():
                yield 'data: {"candidates":[{"content":{"parts":[{"text":"Flutter 3.29 hiện rất ổn định. "}]}}]}\n'.encode("utf-8")
                yield b"data: {truncated_json\n"

            class _BrokenResp:
                def __enter__(self):
                    return _partial_gen()

                def __exit__(self, *a):
                    return False

            return _BrokenResp()

        with patch.object(bot, "GEMINI_API_KEY", "fake-free-tier-key"), patch.object(
            bot, "GEMINI_MODELS", ("gemini-3.1-flash-lite",)
        ), patch("urllib.request.urlopen", side_effect=_partial_sse_after_429):
            partial_out = "".join(bot._stream_gemini_tokens("Tin tức công nghệ mới nhất hôm nay", "SYS"))
            self.assertIn("Flutter 3.29", partial_out)
            self.assertGreater(bot._GEMINI_WEB_SEARCH_COOLDOWN_UNTIL, time.monotonic())
            self.assertNotIn("gemini-3.1-flash-lite", bot._GEMINI_COOLDOWN_UNTIL)

        bot._clear_chat_history()

        def _ws_only_then_err(*_a, **_kw):
            yield "   "
            raise ValueError("truncated empty chunk")

        with patch.object(bot, "GEMINI_API_KEY", "fake-free-tier-key"), patch.object(
            bot, "GEMINI_MODELS", ("gemini-3.1-flash-lite",)
        ), patch.object(bot, "_filter_stream_tokens", side_effect=_ws_only_then_err), patch("urllib.request.urlopen"):
            with self.assertRaises(RuntimeError):
                list(bot._stream_gemini_tokens("Câu hỏi ngắn", "SYS"))
            self.assertEqual(bot.get_chat_history(), [])
            self.assertEqual([c["role"] for c in bot._build_gemini_contents("Câu hỏi kế tiếp")], ["user"])

        bot._clear_chat_history()
        bot._GEMINI_NETWORK_OFFLINE_UNTIL = time.monotonic() + 30.0
        with patch.object(bot, "GEMINI_API_KEY", "fake-free-tier-key"), patch.object(
            bot, "_resolve_ollama_model", return_value=None
        ):
            offline_events = list(bot.stream_reply("Tin tức công nghệ mới nhất hôm nay"))
            offline_searching = [
                e for e in offline_events if e.get("type") == "state" and e.get("state") == "searching"
            ]
            self.assertEqual(offline_searching, [])


if __name__ == "__main__":
    unittest.main()
