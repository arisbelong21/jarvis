import time
import unittest
from unittest import mock

from tests._util import no_audit  # noqa: F401
from core import cloud


class FakeTools:
    def __init__(self):
        self.ran = []

    def declarations(self, ctx=None):
        return [{"name": "home_computer", "description": "x", "parameters": {}}]

    def execute(self, name, args, ctx):
        self.ran.append((name, args))
        return "ok: done"


CALL = {"id": "c1", "name": "home_computer",
        "args": {"action": "command", "instruction": "buka opencode"}, "sig": ""}


class FallbackGate(unittest.TestCase):
    def _brain(self, replies, answered_by, provider_failed):
        tools = FakeTools()
        b = cloud.Brain(tools, log=lambda *_: None)
        seq = list(replies)

        def fake_ask(system, turns, decls):
            b.last_answer = {"model": answered_by, "provider_failed": provider_failed}
            return seq.pop(0)
        b._ask = fake_ask
        return b, tools

    def test_gemini_fallback_does_not_execute(self):
        b, tools = self._brain([{"text": "", "calls": [CALL]}], "gemini-2.5-pro", True)
        said = []
        with mock.patch.object(cloud, "build_system_prompt", lambda d, job=False: "sys"):
            final, turns = b.run([], "buka opencode", ctx={"origin": "telegram", "chat": 1},
                                 on_text=lambda t, f: said.append(t))
        self.assertEqual(tools.ran, [])
        self.assertIn("Belum ada yang saya jalankan", final)
        self.assertEqual(turns[-1]["role"], "pending")
        self.assertTrue(said)

    def test_approval_executes_pending(self):
        b, tools = self._brain([{"text": "Selesai, OpenCode terbuka.", "calls": []}],
                               "provider:x", False)
        pend = {"role": "pending", "calls": [CALL], "ts": time.time()}
        hist = [{"role": "user", "text": "buka opencode"},
                {"role": "model", "text": "Provider kustom sedang gagal…"}, pend]
        with mock.patch.object(cloud, "build_system_prompt", lambda d, job=False: "sys"):
            final, turns = b.run(hist, "ya", ctx={"origin": "telegram", "chat": 1})
        self.assertEqual(len(tools.ran), 1)
        self.assertNotIn("pending", [t["role"] for t in turns])

    def test_cancel_or_stale_does_not_execute(self):
        for text, ts in (("batal", time.time()), ("ya", time.time() - 3600),
                         ("ya tapi ganti ke cursor saja deh", time.time())):
            b, tools = self._brain([{"text": "Oke.", "calls": []}], "provider:x", False)
            hist = [{"role": "user", "text": "buka"},
                    {"role": "pending", "calls": [CALL], "ts": ts}]
            with mock.patch.object(cloud, "build_system_prompt", lambda d, job=False: "sys"):
                b.run(hist, text, ctx={"origin": "telegram", "chat": 1})
            self.assertEqual(tools.ran, [], text)

    def test_provider_answer_executes_normally(self):
        b, tools = self._brain([{"text": "", "calls": [CALL]},
                                {"text": "Sudah.", "calls": []}], "provider:x", False)
        with mock.patch.object(cloud, "build_system_prompt", lambda d, job=False: "sys"):
            b.run([], "buka opencode", ctx={"origin": "telegram", "chat": 1})
        self.assertEqual(len(tools.ran), 1)

    def test_button_text_approves(self):
        self.assertTrue(cloud._fallback_approved(cloud.FALLBACK_YES, {"ts": time.time()}))
        self.assertFalse(cloud._fallback_approved(cloud.FALLBACK_NO, {"ts": time.time()}))


if __name__ == "__main__":
    unittest.main()
