"""
Milestone M3 (P2: UI Chat/Code & State Synchronization — F25..F30) Verification & Adversarial Suite.
Covers:
- F25: Multiline <textarea id="msg" rows="1">, Shift+Enter vs Enter, paste indentation preservation,
       auto-resize (36px..120px), and code-safe Vietnamese normalization (client & server).
- F26: Formatted <pre class="code-block"> with .code-header, .code-lang, .copy-btn ("Sao chép" -> "Đã sao chép!" 2s),
       **bold** markdown outside code, Voice Mode subtitle code-fence filtering, and text-selection click protection.
- F27: Unified Chat (#chat) & Voice (#voiceChat) conversation history persisted in localStorage["wisp_chat_history"]
       across mode switches and F5 reloads (initHistory() + backend sync).
- F28: Synchronized Clear History (DELETE /api/history + UI buttons #clearChatBtn / #clearVoiceChatBtn + "xóa lịch sử" command).
- F29: Profile Popover outside-click / Escape dismiss reverts unsaved edits without calling POST /api/profile;
       saves ONLY when clicking #saveProfileBtn ("Lưu hồ sơ").
- F30: Unused Iconsax CDN link removed from templates/index.html; GEMINI_API_KEY isolated in .env / .env.example.
"""
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from werkzeug.serving import make_server

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import app as flask_app_module
import bot


def _find_headless_chromium_binary() -> str | None:
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        shutil.which("msedge"),
        shutil.which("chrome"),
        shutil.which("chromium"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


class TestMilestone3P2Verification(unittest.TestCase):
    def setUp(self) -> None:
        self._profile_path = bot.USER_PROFILE_FILE
        self._profile_backup: bytes | None = None
        if os.path.exists(self._profile_path):
            with open(self._profile_path, "rb") as f:
                self._profile_backup = f.read()
        bot._USER_PROFILE_CACHE = None
        bot.clear_chat_history()
        self.client = flask_app_module.app.test_client()

    def tearDown(self) -> None:
        bot.clear_chat_history()
        if self._profile_backup is not None:
            with open(self._profile_path, "wb") as f:
                f.write(self._profile_backup)
        bot._USER_PROFILE_CACHE = None

    # ------------------------------------------------------------------
    # F30: Security & Cleanliness (.env isolation & zero external CDNs)
    # ------------------------------------------------------------------
    def test_01_f30_security_and_cleanliness_env_and_no_cdn(self) -> None:
        index_path = os.path.join(ROOT_DIR, "templates", "index.html")
        with open(index_path, "r", encoding="utf-8") as f:
            index_html = f.read()

        # Verify unused Iconsax / external CDN links are completely absent
        self.assertNotIn("cdn.jsdelivr.net", index_html)
        self.assertNotIn("iconsax", index_html.lower().split("<!--")[0])  # Not in <head> links
        self.assertIsNone(
            re.search(r'<link[^>]+href=["\']https?://', index_html, flags=re.IGNORECASE),
            "templates/index.html must not contain external CDN <link> tags",
        )
        self.assertIsNone(
            re.search(r'<script[^>]+src=["\']https?://', index_html, flags=re.IGNORECASE),
            "templates/index.html must not contain external CDN <script> tags",
        )

        # Verify .env.example exists, documents GEMINI_API_KEY, and contains no real leaked key
        env_example_path = os.path.join(ROOT_DIR, ".env.example")
        self.assertTrue(os.path.isfile(env_example_path), ".env.example must exist")
        with open(env_example_path, "r", encoding="utf-8") as f:
            env_example = f.read()
        self.assertIn("GEMINI_API_KEY=", env_example)
        self.assertIsNone(re.search(r"AIza[0-9A-Za-z_-]{20,}", env_example))

        # Verify bot.py and app.py have no hardcoded AIza... keys
        for filename in ("bot.py", "app.py", "static/app.js"):
            with open(os.path.join(ROOT_DIR, filename), "r", encoding="utf-8") as f:
                src = f.read()
            self.assertIsNone(
                re.search(r"AIza[0-9A-Za-z_-]{20,}", src),
                f"{filename} must never contain a hardcoded Google API key",
            )

        gitignore_path = os.path.join(ROOT_DIR, ".gitignore")
        with open(gitignore_path, "r", encoding="utf-8") as f:
            gitignore = f.read()
        self.assertIn(".env", gitignore)

    def test_01b_f30_dotenv_utf8_bom_and_export_prefix(self) -> None:
        test_key_1 = "_WISP_TEST_BOM_GEMINI_KEY"
        test_key_2 = "_WISP_TEST_EXPORT_KEY"
        os.environ.pop(test_key_1, None)
        os.environ.pop(test_key_2, None)
        tmp_fd, tmp_env_path = tempfile.mkstemp(prefix="wisp_bom_", suffix=".env")
        os.close(tmp_fd)
        try:
            # Write with explicit Windows UTF-8 BOM (\xef\xbb\xbf) + export prefix
            with open(tmp_env_path, "wb") as f:
                f.write(
                    b"\xef\xbb\xbf"
                    + f'{test_key_1}="bom_key_ok"\r\nexport {test_key_2}=\'export_val_ok\'\r\n'.encode("utf-8")
                )
            bot._load_local_dotenv(tmp_env_path)
            self.assertEqual(os.environ.get(test_key_1), "bom_key_ok")
            self.assertEqual(os.environ.get(test_key_2), "export_val_ok")
        finally:
            os.environ.pop(test_key_1, None)
            os.environ.pop(test_key_2, None)
            try:
                os.remove(tmp_env_path)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # F25: Code-Safe Vietnamese Normalization & Multiline Preservation
    # ------------------------------------------------------------------
    def test_02_f25_code_safe_vietnamese_normalization_backend(self) -> None:
        # 1. Single-line raw code must NOT expand 'sg' -> 'Sài Gòn' or 'dc' -> 'được'
        code_single = "let sg = 1; let dc = 2"
        self.assertEqual(bot.normalize_vietnamese_input(code_single), "let sg = 1; let dc = 2")

        # 2. Multiline SwiftUI code with indentation must preserve \n, leading spaces, and variable names
        swift_code = (
            "struct WeatherCard: View {\n"
            "    @State private var sg = \"hn\"\n"
            "    let dc = 42\n"
            "    var body: some View {\n"
            "        Text(\"\\(sg) - \\(dc)\")\n"
            "    }\n"
            "}"
        )
        self.assertEqual(bot.normalize_vietnamese_input(swift_code), swift_code)

        # 3. Mixed Vietnamese prose + pasted raw code lines
        mixed = (
            "giải thích đoạn code này ko dc hả:\n"
            "func compute() {\n"
            "    let sg = 10\n"
            "    let dc = sg + 5\n"
            "    return dc\n"
            "}"
        )
        norm_mixed = bot.normalize_vietnamese_input(mixed)
        expected_mixed = (
            "giải thích đoạn code này không được hả:\n"
            "func compute() {\n"
            "    let sg = 10\n"
            "    let dc = sg + 5\n"
            "    return dc\n"
            "}"
        )
        self.assertEqual(norm_mixed, expected_mixed)

        # 4. Inline code identifiers (including double-backtick inline spans) protected while prose normalizes
        inline_q = "biến `sg`, `` `dc` `` trong hàm `hn()` dùng ntn?"
        self.assertEqual(
            bot.normalize_vietnamese_input(inline_q),
            "biến `sg`, `` `dc` `` trong hàm `hn()` dùng như thế nào?",
        )

        # 5. Fenced code block (3-backtick and 4-backtick nested) protected 100%
        fenced_q = "xem giúp mình:\n```dart\nfinal sg = 'ko dc';\n  print(sg);\n```\ntại sao lỗi?"
        self.assertEqual(bot.normalize_vietnamese_input(fenced_q), fenced_q)

        nested_4tick_q = "xem md:\n````markdown\n```swift\nlet sg = 1\nlet dc = 2\n```\n````\nổn ko?"
        expected_4tick_q = "xem md:\n````markdown\n```swift\nlet sg = 1\nlet dc = 2\n```\n````\nổn không?"
        self.assertEqual(bot.normalize_vietnamese_input(nested_4tick_q), expected_4tick_q)

        # 6. Unindented JSON, HTML/JSX tags, standalone function calls, decorators, export, SQL & CLI commands
        raw_json = '{\n"sg": "hn",\n"dc": "ko"\n}'
        self.assertEqual(bot.normalize_vietnamese_input(raw_json), raw_json)

        raw_jsx = '<div id="sg" data-dc="ko">\n  <span>sg dc</span>\n</div>'
        self.assertEqual(bot.normalize_vietnamese_input(raw_jsx), raw_jsx)

        for code_sample in (
            "print(dc)",
            "console.log(sg, dc)",
            "@MainActor func load(dc: Int)",
            "export default function render(dc)",
            "SELECT sg, dc FROM table_hn",
            "cd sg && mkdir dc",
            "git checkout sg",
        ):
            self.assertEqual(
                bot.normalize_vietnamese_input(code_sample),
                code_sample,
                f"Backend normalization corrupted code sample: {code_sample!r}",
            )

        # 7. Normal Vietnamese prose (including parentheses without space & Vietglish) still normalizes properly
        self.assertEqual(
            bot.normalize_vietnamese_input("thoi tiet sg hnay ntn"),
            "thời tiết Sài Gòn hôm nay như thế nào",
        )
        self.assertEqual(
            bot.normalize_vietnamese_input("thoi tiet sg(hnay) ntn"),
            "thời tiết Sài Gòn(hôm nay) như thế nào",
        )
        self.assertEqual(
            bot.normalize_vietnamese_input("flutter có khó ko?"),
            "flutter có khó không?",
        )
        self.assertEqual(
            bot.normalize_vietnamese_input("git dùng ko dc hả?"),
            "git dùng không được hả?",
        )
        self.assertEqual(
            bot.normalize_vietnamese_input("chào quý ơi may gio roi"),
            "chào Wisp ơi mấy giờ rồi",
        )
        self.assertEqual(bot.normalize_vietnamese_input(""), "")
        self.assertEqual(bot.normalize_vietnamese_input("   \n\t  \r\n "), "")

    def test_03_f25_profile_autolearn_ignores_code_blocks(self) -> None:
        initial_prof = bot.get_user_profile()
        code_with_personal_strings = (
            "Sửa giúp mình đoạn test này:\n"
            "```swift\n"
            'let sample = "mình tên là Trần Văn Bảy, mình 88 tuổi, mình sống ở Hà Nội"\n'
            "```\n"
            "và chuỗi inline `mình tên là Lê Thị Tám` nhé."
        )
        learned = bot.extract_and_update_profile_from_message(code_with_personal_strings)
        self.assertEqual(learned, {})
        after_prof = bot.get_user_profile()
        self.assertEqual(after_prof.get("name"), initial_prof.get("name"))
        self.assertEqual(after_prof.get("age"), initial_prof.get("age"))
        self.assertEqual(after_prof.get("city"), initial_prof.get("city"))

    # ------------------------------------------------------------------
    # F27 & F28: Backend /api/history (GET, POST restore, DELETE clear)
    # ------------------------------------------------------------------
    def test_04_f27_f28_history_api_clear_and_restore(self) -> None:
        # 1. Populate multi-turn history via /api/chat
        r1 = self.client.post("/api/chat", json={"message": "mấy giờ rồi"})
        self.assertEqual(r1.status_code, 200)
        r2 = self.client.post("/api/chat", json={"message": "thời tiết Đà Lạt"})
        self.assertEqual(r2.status_code, 200)

        hist_res = self.client.get("/api/history")
        self.assertEqual(hist_res.status_code, 200)
        hist_data = hist_res.get_json()
        self.assertTrue(hist_data["ok"])
        self.assertEqual(len(hist_data["history"]), 4)
        self.assertEqual(bot._detect_city_in_text("mặc gì hợp?"), "da lat")

        # 2. DELETE /api/history clears backend _CHAT_HISTORY completely
        del_res = self.client.delete("/api/history")
        self.assertEqual(del_res.status_code, 200)
        del_data = del_res.get_json()
        self.assertEqual(del_data, {"ok": True, "message": "Đã xóa toàn bộ lịch sử hội thoại"})
        self.assertEqual(bot.get_chat_history(), [])
        self.assertNotEqual(bot._detect_city_in_text("mặc gì hợp?"), "da lat")

        # 3. POST /api/history restores F5 client localStorage history when backend RAM is empty
        saved_client_history = [
            {"role": "user", "content": "thời tiết Hà Nội"},
            {"role": "assistant", "content": "🌤️ Thời tiết hiện tại ở Hà Nội: 25°C."},
            {"role": "invalid_role", "content": "bỏ qua"},
            {"role": "user", "content": "   "},
        ]
        restore_res = self.client.post(
            "/api/history",
            json={"history": saved_client_history, "replace_if_empty": True},
        )
        self.assertEqual(restore_res.status_code, 200)
        restore_data = restore_res.get_json()
        self.assertTrue(restore_data["ok"])
        self.assertEqual(restore_data["count"], 2)
        self.assertEqual(len(bot.get_chat_history()), 2)
        self.assertEqual(bot._detect_city_in_text("ngày mai thế nào?"), "ha noi")

        # 4. POST /api/history with replace_if_empty=True must NOT overwrite non-empty backend history
        no_clobber_res = self.client.post(
            "/api/history",
            json={
                "history": [{"role": "user", "content": "thời tiết Cần Thơ"}],
                "replace_if_empty": True,
            },
        )
        self.assertEqual(no_clobber_res.status_code, 200)
        self.assertEqual(bot._detect_city_in_text("ngày mai thế nào?"), "ha noi")

    # ------------------------------------------------------------------
    # F25, F26, F28: Rendered HTML & CSS Contracts
    # ------------------------------------------------------------------
    def test_05_html_and_css_structural_contracts(self) -> None:
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        page = resp.get_data(as_text=True)

        # <textarea id="msg" rows="1"> replaces <input type="text" id="msg">
        self.assertIsNotNone(
            re.search(r'<textarea\b[^>]*\bid=["\']msg["\'][^>]*\brows=["\']1["\']', page)
        )
        self.assertIsNone(re.search(r'<input\b[^>]*\bid=["\']msg["\']', page))

        # Both Clear History buttons exist
        self.assertIn('id="clearChatBtn"', page)
        self.assertIn('id="clearVoiceChatBtn"', page)

        css_path = os.path.join(ROOT_DIR, "static", "style.css")
        with open(css_path, "r", encoding="utf-8") as f:
            css = f.read()
        self.assertIn("max-height: 120px", css)
        self.assertIn(".code-block", css)
        self.assertIn(".code-header", css)
        self.assertIn(".code-lang", css)
        self.assertIn(".copy-btn", css)
        self.assertIn(".copy-btn.copied", css)

    # ------------------------------------------------------------------
    # REAL HEADLESS CHROMIUM E2E VERIFICATION (F25, F26, F27, F28, F29)
    # ------------------------------------------------------------------
    def test_06_real_browser_headless_e2e_all_p2_features(self) -> None:
        browser_bin = _find_headless_chromium_binary()
        self.assertIsNotNone(browser_bin, "Edge or Chrome binary is required on Windows for real DOM E2E test")

        report_holder: dict = {}
        report_event = threading.Event()

        e2e_js_runner = r"""
        <script>
        (async function runM3BrowserE2E() {
          const results = { checks: {}, errors: [] };
          const assert = (name, cond, detail = "") => {
            results.checks[name] = Boolean(cond);
            if (!cond) {
              results.errors.push(name + (detail ? ": " + detail : ""));
            }
          };
          const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

          try {
            // Start from clean state
            localStorage.removeItem("wisp_chat_history");
            await window.clearAllConversationHistory();

            // --- F25: Multiline <textarea>, code-safe normalization, paste & Shift+Enter ---
            const msgEl = document.getElementById("msg");
            assert("f25_is_textarea", msgEl && msgEl.tagName === "TEXTAREA" && msgEl.rows === 1);

            const normCode = window.normalizeVietnameseClientInput("let sg = 1; let dc = 2");
            assert("f25_client_norm_preserves_code_vars", normCode === "let sg = 1; let dc = 2", normCode);

            const multilineSwift = "struct Demo {\n    let sg = 1\n    let dc = 2\n}";
            const normSwift = window.normalizeVietnameseClientInput(multilineSwift);
            assert("f25_client_norm_preserves_multiline_indent", normSwift === multilineSwift, normSwift);

            const normProse = window.normalizeVietnameseClientInput("thoi tiet sg hnay ntn");
            assert(
              "f25_client_norm_prose_works",
              normProse === "thời tiết Sài Gòn hôm nay như thế nào",
              normProse
            );

            // Synthetic paste of 8-line indented code into #msg
            const eightLineCode = [
              "func renderCard() {",
              "    let sg = 10",
              "    let dc = 20",
              "    if sg < dc {",
              "        print(sg)",
              "    }",
              "    return",
              "}"
            ].join("\r\n");
            msgEl.value = "";
            const dt = new DataTransfer();
            dt.setData("text/plain", eightLineCode);
            const pasteEvt = new ClipboardEvent("paste", {
              bubbles: true,
              cancelable: true,
              clipboardData: dt,
            });
            msgEl.dispatchEvent(pasteEvt);
            assert(
              "f25_paste_preserves_indentation_and_newlines",
              msgEl.value === eightLineCode.replace(/\r\n/g, "\n"),
              msgEl.value
            );
            const expandedH = parseFloat(msgEl.style.height || "0");
            assert(
              "f25_autoresize_clamped_to_120px",
              expandedH > 36 && expandedH <= 120 && msgEl.style.overflowY === "auto",
              `height=${msgEl.style.height}, overflowY=${msgEl.style.overflowY}`
            );

            // Shift+Enter must NOT preventDefault and must NOT send message
            const shiftEnterEvt = new KeyboardEvent("keydown", {
              key: "Enter",
              shiftKey: true,
              bubbles: true,
              cancelable: true,
            });
            const notCancelled = msgEl.dispatchEvent(shiftEnterEvt);
            assert("f25_shift_enter_does_not_send", notCancelled === true && document.getElementById("chat").children.length === 0);

            // Reset input -> shrinks back to 1-line 36px
            msgEl.value = "";
            msgEl.dispatchEvent(new Event("input", { bubbles: true }));
            assert("f25_autoresize_shrinks_back", msgEl.style.height === "36px", msgEl.style.height);

            // --- F26: Formatted <pre><code>, Copy button 2s feedback, XSS safety, bold, selection protection & voice subtitle ---
            const sampleMd = "Lưu ý **quan trọng** khi dùng `mấy giờ`:\n```swift\nlet sg = \"<script>window.__xss=1<\\/script>\"\n```";
            const renderedHtml = window.formatSafeMessageHtml(sampleMd);
            assert("f26_no_xss_script_tag", !renderedHtml.includes("<script>") && !window.__xss);
            assert("f26_bold_rendered", renderedHtml.includes("<strong>quan trọng</strong>"));
            assert("f26_inline_code_rendered", renderedHtml.includes('<code class="inline-code">mấy giờ</code>'));
            assert(
              "f26_pre_code_header_and_copy_btn",
              renderedHtml.includes('<pre class="code-block">') &&
                renderedHtml.includes('<div class="code-header">') &&
                renderedHtml.includes('<span class="code-lang">swift</span>') &&
                renderedHtml.includes('class="copy-btn"') &&
                renderedHtml.includes('<code class="language-swift">')
            );

            // Unclosed streaming code fence renders live <pre>
            const streamingMd = "Đang viết:\n```python\ndef add(a, b):\n    return a + b";
            const streamingHtml = window.formatSafeMessageHtml(streamingMd);
            assert(
              "f26_streaming_unclosed_fence_renders_pre",
              streamingHtml.includes('<pre class="code-block">') &&
                streamingHtml.includes('<code class="language-python">')
            );

            // Mount sampleMd inside #chat to test Copy button & selection guard
            const chatEl = document.getElementById("chat");
            const voiceChatEl = document.getElementById("voiceChat");
            chatEl.innerHTML = `<div class="message-row bot-row"><div class="message bot-message">${renderedHtml}</div></div>`;
            const copyBtn = chatEl.querySelector(".copy-btn");
            copyBtn.click();
            await sleep(60);
            assert(
              "f26_copy_btn_shows_copied_feedback",
              copyBtn.textContent === "Đã sao chép!" && copyBtn.classList.contains("copied"),
              copyBtn.textContent
            );
            assert("f26_copy_btn_does_not_trigger_send", chatEl.children.length === 1);
            await sleep(2100);
            assert(
              "f26_copy_btn_reverts_after_2s",
              copyBtn.textContent === "Sao chép" && !copyBtn.classList.contains("copied"),
              copyBtn.textContent
            );

            // Active text selection inside .inline-code must NOT trigger sendMessage or orb click
            const inlineCodeEl = chatEl.querySelector(".inline-code");
            const sel = window.getSelection();
            const range = document.createRange();
            range.selectNodeContents(inlineCodeEl);
            sel.removeAllRanges();
            sel.addRange(range);
            inlineCodeEl.click();
            await sleep(50);
            assert("f26_selecting_inline_code_does_not_send", chatEl.children.length === 1);

            const statusBeforeOrbClick = document.getElementById("typingText").textContent;
            document.getElementById("orbModeBtn").click();
            assert(
              "f26_selection_prevents_orb_click",
              document.getElementById("typingText").textContent === statusBeforeOrbClick
            );
            sel.removeAllRanges();

            // Voice subtitle filters out fenced code blocks
            window.switchAppMode("voice");
            setVoiceSubtitle("Giải thích ngắn:\n```swift\nlet secretCodeLine = 12345\n```", false);
            const subText = document.getElementById("voiceTranscript").textContent;
            assert("f26_voice_subtitle_strips_code_fence", !subText.includes("secretCodeLine"), subText);
            window.switchAppMode("chat");

            // --- F27: Unified Chat & Voice History across mode switches & F5 initHistory() ---
            await window.clearAllConversationHistory();
            await window.sendMessage("mấy giờ rồi");
            assert("f27_turn1_in_chat_dom", chatEl.children.length === 2, String(chatEl.children.length));
            assert("f27_turn1_synced_in_voice_dom", voiceChatEl.children.length === 2, String(voiceChatEl.children.length));

            window.switchAppMode("voice");
            assert("f27_switch_to_voice_keeps_dom", voiceChatEl.children.length === 2);
            await window.sendMessage("hôm nay thứ mấy");
            assert("f27_turn2_in_voice_dom", voiceChatEl.children.length === 4, String(voiceChatEl.children.length));
            assert("f27_turn2_synced_in_chat_dom", chatEl.children.length === 4, String(chatEl.children.length));

            window.switchAppMode("chat");
            const rawStored = localStorage.getItem("wisp_chat_history");
            const parsedStored = rawStored ? JSON.parse(rawStored) : [];
            assert("f27_localstorage_has_4_entries", parsedStored.length === 4, String(parsedStored.length));

            // Simulate F5 reload: wipe both DOM containers and run initHistory()
            chatEl.innerHTML = "";
            voiceChatEl.innerHTML = "";
            window.initHistory();
            assert("f27_f5_restores_chat_dom", chatEl.children.length === 4, String(chatEl.children.length));
            assert("f27_f5_restores_voice_dom", voiceChatEl.children.length === 4, String(voiceChatEl.children.length));
            assert(
              "f27_f5_chronological_order",
              chatEl.children[0].textContent.includes("mấy giờ") &&
                chatEl.children[2].textContent.includes("hôm nay thứ mấy")
            );

            // --- F28: Synchronized Clear History (UI button & "xóa lịch sử" command) ---
            document.getElementById("clearChatBtn").click();
            await sleep(250);
            const backendAfterBtnClear = await (await fetch("/api/history")).json();
            assert(
              "f28_button_clears_ui_localstorage_and_backend",
              chatEl.children.length === 0 &&
                voiceChatEl.children.length === 0 &&
                localStorage.getItem("wisp_chat_history") === null &&
                Array.isArray(backendAfterBtnClear.history) &&
                backendAfterBtnClear.history.length === 0
            );

            // Populate again, then clear via typed command "xoa lich su"
            await window.sendMessage("mấy giờ rồi");
            assert("f28_repopulated_before_text_cmd", chatEl.children.length === 2);
            await window.sendMessage("xoa lich su");
            const backendAfterCmdClear = await (await fetch("/api/history")).json();
            assert(
              "f28_text_cmd_clears_ui_localstorage_and_backend",
              chatEl.children.length === 0 &&
                voiceChatEl.children.length === 0 &&
                localStorage.getItem("wisp_chat_history") === null &&
                backendAfterCmdClear.history.length === 0
            );

            // --- F29: Profile Popover outside-click reverts unsaved edits without POST /api/profile ---
            await sleep(150);
            const profBefore = await (await fetch("/api/profile")).json();
            const savedName = profBefore.profile.name;
            const profileBtn = document.getElementById("userProfileBtn");
            const profilePanel = document.getElementById("userProfilePanel");
            const nameInput = document.getElementById("profNameInput");
            const saveBtn = document.getElementById("saveProfileBtn");

            profileBtn.click();
            assert("f29_popover_opens", !profilePanel.hasAttribute("hidden"));
            nameInput.value = "Tên Gõ Dở Không Được Lưu";
            nameInput.dispatchEvent(new Event("input", { bubbles: true }));

            // Click outside #userProfileWrap
            document.body.dispatchEvent(new MouseEvent("click", { bubbles: true }));
            await sleep(200);
            const profAfterOutsideClick = await (await fetch("/api/profile")).json();
            assert("f29_outside_click_hides_popover", profilePanel.hasAttribute("hidden") && !profilePanel.classList.contains("active"));
            assert("f29_outside_click_reverts_input", nameInput.value === savedName, `got=${nameInput.value}, expected=${savedName}`);
            assert(
              "f29_outside_click_does_not_save_to_server",
              profAfterOutsideClick.profile.name === savedName,
              profAfterOutsideClick.profile.name
            );

            // Explicit click on #saveProfileBtn DOES save to POST /api/profile
            profileBtn.click();
            nameInput.value = "Khánh Test P2";
            saveBtn.click();
            await sleep(250);
            const profAfterExplicitSave = await (await fetch("/api/profile")).json();
            assert(
              "f29_explicit_save_btn_persists_to_server",
              profAfterExplicitSave.profile.name === "Khánh Test P2" && profilePanel.hasAttribute("hidden"),
              profAfterExplicitSave.profile.name
            );

            // --- Adversarial Edge Cases (IME composition, unindented JSON, multi-fence, corrupted localStorage, Escape revert) ---
            const rawJsonClient = '{\n"sg": "hn",\n"dc": "ko"\n}';
            assert(
              "adv_f25_unindented_json_preserved",
              window.normalizeVietnameseClientInput(rawJsonClient) === rawJsonClient
            );

            // IME composition Enter must not send message
            msgEl.value = "mấy giờ";
            const imeEvt = new KeyboardEvent("keydown", {
              key: "Enter",
              isComposing: true,
              bubbles: true,
              cancelable: true,
            });
            msgEl.dispatchEvent(imeEvt);
            await sleep(50);
            assert("adv_f25_ime_enter_ignored", chatEl.children.length === 0 && msgEl.value === "mấy giờ");
            msgEl.value = "";
            msgEl.dispatchEvent(new Event("input", { bubbles: true }));

            // Markdown **stars** and `backticks` inside fenced code block must remain literal
            const multiFenceMd = "```\nconst p = a ** b;\nconst t = `sg`;\n```\nVà **đậm**";
            const multiFenceHtml = window.formatSafeMessageHtml(multiFenceMd);
            assert(
              "adv_f26_no_markdown_mutation_inside_pre",
              multiFenceHtml.includes('<span class="code-lang">code</span>') &&
                multiFenceHtml.includes('<code class="language-plaintext">const p = a ** b;\nconst t = `sg`;</code>') &&
                multiFenceHtml.includes("<strong>đậm</strong>")
            );

            // Corrupted localStorage JSON must not crash initHistory()
            localStorage.setItem("wisp_chat_history", "{corrupted_json_payload!!!");
            window.initHistory();
            assert("adv_f27_corrupted_localstorage_recovers", chatEl.children.length === 0 && voiceChatEl.children.length === 0);

            // #clearVoiceChatBtn also clears both panes + backend
            await window.sendMessage("mấy giờ rồi");
            assert("adv_f28_pre_voice_btn_clear", chatEl.children.length === 2 && voiceChatEl.children.length === 2);
            document.getElementById("clearVoiceChatBtn").click();
            await sleep(250);
            const backendAfterVoiceBtn = await (await fetch("/api/history")).json();
            assert(
              "adv_f28_voice_clear_btn_clears_all",
              chatEl.children.length === 0 &&
                voiceChatEl.children.length === 0 &&
                backendAfterVoiceBtn.history.length === 0
            );

            // Escape key on Profile Popover reverts unsaved edits without POST /api/profile
            profileBtn.click();
            nameInput.value = "Sửa Rồi Nhấn Escape";
            window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
            await sleep(150);
            const profAfterEsc = await (await fetch("/api/profile")).json();
            assert(
              "adv_f29_escape_reverts_and_hides_without_save",
              profilePanel.hasAttribute("hidden") &&
                nameInput.value === "Khánh Test P2" &&
                profAfterEsc.profile.name === "Khánh Test P2"
            );

            // --- Reviewer Attack Suite (Client Code/SQL/CLI Norm, 4-Backtick Nested Fence, Mid-Stream Barge-In + F5 Sync, Clear-During-Stream Race, Hands-Free Barge-In, Mic Toggle With Selection) ---
            for (const sample of [
              "print(dc)",
              "@MainActor func load(dc: Int)",
              "export default function render(dc)",
              "SELECT sg, dc FROM table_hn",
              "cd sg && mkdir dc",
              "git checkout sg",
            ]) {
              const actualNorm = window.normalizeVietnameseClientInput(sample);
              assert(
                "rev_f25_client_preserves_" + sample.slice(0, 12).replace(/\W+/g, "_"),
                actualNorm === sample,
                `got=${actualNorm}`
              );
            }
            const parenProse = window.normalizeVietnameseClientInput("thoi tiet sg(hnay) ntn");
            assert(
              "rev_f25_client_prose_parens_normalized",
              parenProse === "thời tiết Sài Gòn(hôm nay) như thế nào",
              parenProse
            );
            const vietglishProse = window.normalizeVietnameseClientInput("flutter có khó ko?");
            assert(
              "rev_f25_client_vietglish_normalized",
              vietglishProse === "flutter có khó không?",
              vietglishProse
            );

            // Attack 1: 4-backtick outer fence wrapping 3-backtick inner fence
            const nested4FenceMd = "````markdown\n```swift\nlet sg = 1\n```\n````";
            const nested4Html = window.formatSafeMessageHtml(nested4FenceMd);
            assert(
              "rev_f26_nested_4backtick_fence_single_block",
              (nested4Html.match(/<pre class="code-block">/g) || []).length === 1 &&
                nested4Html.includes('<span class="code-lang">markdown</span>') &&
                nested4Html.includes("```swift\nlet sg = 1\n```"),
              nested4Html
            );
            window.switchAppMode("voice");
            setVoiceSubtitle("Xem tài liệu:\n" + nested4FenceMd, false);
            const nestedSub = document.getElementById("voiceTranscript").textContent;
            assert(
              "rev_f26_voice_subtitle_strips_4backtick_fence",
              !nestedSub.includes("let sg = 1") && !nestedSub.includes("```"),
              nestedSub
            );

            // Explicit #voiceMicToggleBtn works even when text is actively selected in #voiceChat / #chat
            chatEl.innerHTML = '<div class="message-row bot-row"><div class="message bot-message"><span id="selProbe">đoạn text đang bôi đen</span></div></div>';
            const sel2 = window.getSelection();
            const range2 = document.createRange();
            range2.selectNodeContents(document.getElementById("selProbe"));
            sel2.removeAllRanges();
            sel2.addRange(range2);
            const mutedBeforeBtn = window.__wispVoiceDebug.getState().voiceMicMuted;
            document.getElementById("voiceMicToggleBtn").click();
            const mutedAfterBtn = window.__wispVoiceDebug.getState().voiceMicMuted;
            assert("rev_f26_mic_btn_works_despite_selection", mutedAfterBtn !== mutedBeforeBtn);
            if (mutedAfterBtn) {
              document.getElementById("voiceMicToggleBtn").click();
            }
            sel2.removeAllRanges();

            // Hands-Free Acoustic Voice Barge-in ("Tự ngắt lời không cần chạm")
            window.__wispVoiceDebug.setSpeakingState(true, 1000);
            const profileMock = { noiseGate: 38 };
            // 1) Within 320ms onset grace window -> ignored
            const trigGrace = window.evaluateHandsFreeVoiceBargeIn(95, profileMock, 1180);
            assert("rev_voice_bargein_ignores_onset_grace", trigGrace === false && window.__wispVoiceDebug.getState().isSpeakingTTS === true);
            // 2) Short <240ms click/cough spike after grace window -> ignored
            const trigSpikeStart = window.evaluateHandsFreeVoiceBargeIn(95, profileMock, 1400);
            const trigSpikeMid = window.evaluateHandsFreeVoiceBargeIn(95, profileMock, 1550);
            const trigSpikeDrop = window.evaluateHandsFreeVoiceBargeIn(20, profileMock, 1580);
            assert(
              "rev_voice_bargein_ignores_short_noise_spike",
              !trigSpikeStart && !trigSpikeMid && !trigSpikeDrop && window.__wispVoiceDebug.getState().isSpeakingTTS === true
            );
            // 3) Sustained >=240ms human voice above noiseGate+26 -> auto-interrupts hands-free!
            const trigSustain0 = window.evaluateHandsFreeVoiceBargeIn(92, profileMock, 1700);
            const trigSustain1 = window.evaluateHandsFreeVoiceBargeIn(92, profileMock, 1955);
            assert(
              "rev_voice_bargein_triggers_hands_free_after_240ms",
              trigSustain0 === false && trigSustain1 === true && window.__wispVoiceDebug.getState().isSpeakingTTS === false
            );
            window.switchAppMode("chat");

            // Attack 2: Mid-stream Barge-in of unclosed code fence + F5 initHistory() syncing full turns to Backend
            await window.clearAllConversationHistory();
            await window.sendMessage("mấy giờ rồi");
            assert("rev_f27_turn1_committed", chatEl.children.length === 2);

            const slowStreamPromise1 = window.sendMessage("__slow_stream_code_test__");
            await sleep(140);
            window.interruptCurrentInteraction();
            await slowStreamPromise1;
            await sleep(60);
            assert(
              "rev_f26_aborted_unclosed_fence_renders_valid_pre",
              chatEl.children.length === 4 &&
                chatEl.children[3].querySelectorAll("pre.code-block").length === 1 &&
                chatEl.children[3].textContent.includes("let partial = 1") &&
                !chatEl.children[3].textContent.includes("NEVER_ARRIVES")
            );
            // Simulate F5 reload when backend only had Turn 1 (2 entries) while localStorage has Turn 1 + aborted Turn 2 (4 entries)
            chatEl.innerHTML = "";
            voiceChatEl.innerHTML = "";
            await window.initHistory(true);
            const backendAfterF5Sync = await (await fetch("/api/history")).json();
            assert(
              "rev_f27_f5_syncs_aborted_turn_to_nonempty_backend",
              chatEl.children.length === 4 &&
                Array.isArray(backendAfterF5Sync.history) &&
                backendAfterF5Sync.history.length === 4
            );

            // Clear-While-Streaming Race Attack: clearAllConversationHistory() during active stream must leave DOM, backend, and localStorage === null
            const slowStreamPromise2 = window.sendMessage("__slow_stream_code_test__");
            await sleep(120);
            await window.clearAllConversationHistory();
            await slowStreamPromise2;
            await sleep(80);
            const backendAfterRaceClear = await (await fetch("/api/history")).json();
            assert(
              "rev_f28_clear_during_active_stream_no_ghost_turn_or_ls_array",
              chatEl.children.length === 0 &&
                voiceChatEl.children.length === 0 &&
                localStorage.getItem("wisp_chat_history") === null &&
                Array.isArray(backendAfterRaceClear.history) &&
                backendAfterRaceClear.history.length === 0,
              `chat=${chatEl.children.length}, ls=${localStorage.getItem("wisp_chat_history")}`
            );
          } catch (err) {
            results.errors.push("UNCAUGHT: " + (err && err.stack ? err.stack : String(err)));
          }

          await fetch("/__m3_e2e_report__", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(results),
          });
        })();
        </script>
        """

        app_obj = flask_app_module.app

        def _e2e_wsgi_app(environ, start_response):
            path = environ.get("PATH_INFO", "")
            if path == "/__m3_e2e_harness__":
                with app_obj.test_request_context("/"):
                    rendered = flask_app_module.index()
                body_bytes = rendered.replace("</body>", e2e_js_runner + "\n</body>").encode("utf-8")
                start_response(
                    "200 OK",
                    [
                        ("Content-Type", "text/html; charset=utf-8"),
                        ("Content-Length", str(len(body_bytes))),
                    ],
                )
                return [body_bytes]
            if path == "/api/chat/stream" and environ.get("REQUEST_METHOD") == "POST":
                length = int(environ.get("CONTENT_LENGTH") or 0)
                raw_req = environ["wsgi.input"].read(length)
                try:
                    req_json = json.loads(raw_req.decode("utf-8", errors="replace"))
                except Exception:
                    req_json = {}
                if req_json.get("message") == "__slow_stream_code_test__":
                    start_response(
                        "200 OK",
                        [
                            ("Content-Type", "application/x-ndjson; charset=utf-8"),
                            ("Cache-Control", "no-cache"),
                        ],
                    )

                    def _slow_ndjson():
                        yield (
                            json.dumps({"type": "normalized", "text": "__slow_stream_code_test__"}) + "\n"
                        ).encode("utf-8")
                        yield (
                            json.dumps(
                                {
                                    "type": "token",
                                    "delta": "Đang viết mẫu:\n```swift\nlet partial = 1\n",
                                },
                                ensure_ascii=False,
                            )
                            + "\n"
                        ).encode("utf-8")
                        time.sleep(0.55)
                        yield (
                            json.dumps(
                                {"type": "token", "delta": "let tail = 'NEVER_ARRIVES'\n```"},
                                ensure_ascii=False,
                            )
                            + "\n"
                        ).encode("utf-8")
                        yield (json.dumps({"type": "done"}) + "\n").encode("utf-8")

                    return _slow_ndjson()
                # Replay body to Flask app for normal /api/chat/stream calls
                import io

                environ["wsgi.input"] = io.BytesIO(raw_req)
            if path == "/__m3_e2e_report__" and environ.get("REQUEST_METHOD") == "POST":
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

        temp_profile_dir = tempfile.mkdtemp(prefix="wisp_m3_edge_")
        proc = None
        try:
            url = f"http://127.0.0.1:{port}/__m3_e2e_harness__"
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
            self.assertTrue(finished, "Timed out waiting for headless browser E2E report")
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
        self.assertEqual(errors, [], f"Browser E2E checks failed: {errors}\nAll checks: {json.dumps(checks, indent=2)}")
        self.assertGreaterEqual(len(checks), 24, f"Expected >=24 browser assertions, got {len(checks)}")
        for check_name, passed in checks.items():
            self.assertTrue(passed, f"Browser assertion {check_name} was False")


if __name__ == "__main__":
    unittest.main()
