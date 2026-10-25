import unittest
from unittest import mock

from tests._util import no_audit, no_trust  # noqa: F401  (sys.path)
from core import decision_loop as dl


class Approval(unittest.TestCase):
    def test_word_boundary(self):
        self.assertTrue(dl.is_approval("ya, lanjut"))
        self.assertTrue(dl.is_approval("Oke gas"))
        self.assertFalse(dl.is_approval("tolong klik tombol saya"))   # 'ya' in 'saya'
        self.assertFalse(dl.is_approval("hanya bayar token"))         # 'ya', 'ok'


class Gate(unittest.TestCase):
    def setUp(self):
        self.p1, self.p2 = no_trust(), no_audit()
        self.p1.start(); self.p2.start()

    def tearDown(self):
        self.p1.stop(); self.p2.stop()

    def test_retry_click_whitelisted(self):
        d, why = dl.gate("computer_control", {"action": "screen_click",
                         "description": "Retry button in the WorkBuddy window"})
        self.assertEqual(d, "allow", why)
        d, _ = dl.gate("computer_control", {"action": "screen_click",
                       "description": "tombol Coba lagi di OpenCode"})
        self.assertEqual(d, "allow")

    def test_dangerous_click_still_confirms(self):
        for desc in ("Delete all button", "tombol Kirim email", "Retry and discard changes"):
            d, _ = dl.gate("computer_control", {"action": "screen_click", "description": desc})
            self.assertEqual(d, "confirm", desc)
        d, _ = dl.gate("computer_control", {"action": "type", "text": "retry"})
        self.assertEqual(d, "confirm")

    def test_deny_wins(self):
        cfg = {"tool_permissions": {"deny": ["computer_control"]}}
        d, _ = dl.gate("computer_control", {"action": "screen_click",
                       "description": "Retry"}, config=cfg)
        self.assertEqual(d, "deny")

    def test_explicit_never_lifts_destructive(self):
        self.assertEqual(dl.gate("undo", {}, explicit=True)[0], "confirm")
        self.assertEqual(dl.gate("open_app", {"app_name": "x"}, explicit=True)[0], "allow")


class Terminal(unittest.TestCase):
    def setUp(self):
        self.p1 = no_trust(); self.p1.start()

    def tearDown(self):
        self.p1.stop()

    def test_read_commands_allowed(self):
        for c in ("git status", "dir", "Get-ChildItem C:\\x", "python --version",
                  "git log --oneline | findstr fix", "pip list"):
            self.assertEqual(dl.classify_command(c), "read", c)
            self.assertEqual(dl.gate("run_command", {"command": c})[0], "allow", c)

    def test_changing_commands_need_confirmation(self):
        for c in ("del /s /q C:\\proj", "Remove-Item -Recurse x", "format D:",
                  "reg add HKCU\\x", "shutdown /s /t 0", "pip install requests",
                  "git push origin main", "git reset --hard HEAD~1",
                  "echo hi > file.txt", "npm test", "rm -rf /", "winget install x"):
            self.assertNotEqual(dl.classify_command(c), "read", c)
            self.assertEqual(dl.gate("run_command", {"command": c})[0], "confirm", c)

    def test_trust_cannot_lift_terminal(self):
        with mock.patch("core.agent.permissions._trust_enabled", lambda: True), \
             mock.patch("core.tool_trust.is_trusted", lambda *a, **k: True):
            self.assertEqual(dl.gate("run_command", {"command": "del x"})[0], "confirm")


class Verify(unittest.TestCase):
    def test_open_app(self):
        ok, _ = dl.verify("open_app", {}, "Opened OpenCode — verified: process opencode.exe.")
        self.assertTrue(ok)
        ok, txt = dl.verify("open_app", {}, "A different app opened: Cursor, not OpenCode.")
        self.assertFalse(ok)
        self.assertIn("UNVERIFIED", txt)

    def test_run_command(self):
        self.assertTrue(dl.verify("run_command", {}, "exit_code: 0\nstdout:\n")[0])
        self.assertFalse(dl.verify("run_command", {}, "exit_code: 1\n")[0])

    def test_email_needs_id(self):
        a = {"action": "gmail_send"}
        self.assertTrue(dl.verify("composio_actions", a,
                                  "Email terkirim ke a@b.co — subjek “x” (message_id: 18a).")[0])
        self.assertIsNone(dl.verify("composio_actions", a,
                                    "KONFIRMASI DIPERLUKAN — kirim email")[0])
        self.assertFalse(dl.verify("composio_actions", a, "Composio menolak: 401")[0])

    def test_file_write_checked_on_disk(self):
        ok, txt = dl.verify("file_controller", {"action": "write",
                            "path": "/tmp/__jarvis_surely_missing__.txt"}, "Saved.")
        self.assertFalse(ok)
        self.assertIn("does not exist", txt)


class Honesty(unittest.TestCase):
    def test_false_claim_replaced(self):
        dl.note_outcome("open_app", False, "A different app opened: Cursor, not OpenCode.")
        out = dl.honest_reply("Sudah saya bukakan OpenCode.", since=0)
        self.assertIn("Cursor", out)
        self.assertNotIn("Sudah saya", out)

    def test_true_claim_kept(self):
        dl.note_outcome("open_app", True, "Opened OpenCode — verified")
        self.assertEqual(dl.honest_reply("OpenCode sudah terbuka.", 0), "OpenCode sudah terbuka.")


class Retry(unittest.TestCase):
    def test_transient_read_tool_retried(self):
        calls = []

        def fn():
            calls.append(1)
            return "ERROR: timed out" if len(calls) < 2 else "result"
        out = dl.run_with_retry("web_search", {}, fn, sleep=lambda s: None)
        self.assertEqual(out, "result")
        self.assertEqual(len(calls), 2)

    def test_outward_tool_never_retried(self):
        calls = []

        def fn():
            calls.append(1)
            return "ERROR: timed out"
        out = dl.run_with_retry("send_message", {}, fn, sleep=lambda s: None)
        self.assertEqual(len(calls), 1)
        self.assertIn("[FAILED", out)

    def test_exception_reported_not_swallowed(self):
        def fn():
            raise RuntimeError("boom")
        out = dl.run_with_retry("open_app", {}, fn, sleep=lambda s: None)
        self.assertIn("boom", out)
        self.assertIn("[FAILED", out)


class Dispatch(unittest.TestCase):
    def test_confirm_blocks_execution(self):
        ran = []
        with no_audit(), no_trust():
            out = dl.dispatch("run_command", {"command": "del x"},
                              lambda t, a: ran.append(1) or "exit_code: 0",
                              source="test")
        self.assertEqual(out["decision"], "confirm")
        self.assertEqual(ran, [])


if __name__ == "__main__":
    unittest.main()
