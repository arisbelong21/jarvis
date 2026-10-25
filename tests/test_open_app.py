import unittest
from unittest import mock

from tests._util import no_audit  # noqa: F401
from actions import open_app as oa


class Dictionary(unittest.TestCase):
    def test_distinct_entries(self):
        ids = {n: (oa.lookup_app(n) or (None,))[0] for n in
               ("opencode", "Open Code", "cursor", "vs code", "code", "workbuddy",
                "whatsapp", "telegram", "chrome", "edge")}
        self.assertEqual(ids["opencode"], "opencode")
        self.assertEqual(ids["Open Code"], "opencode")
        self.assertEqual(ids["cursor"], "cursor")
        self.assertEqual(ids["vs code"], "vscode")
        self.assertEqual(ids["code"], "vscode")
        self.assertEqual(ids["workbuddy"], "workbuddy")
        self.assertEqual(len({ids["opencode"], ids["cursor"], ids["code"], ids["workbuddy"]}), 4)

    def test_no_fuzzy_match(self):
        for n in ("open code please", "opencod", "codex", "my editor", "zzz"):
            self.assertIsNone(oa.lookup_app(n), n)


class Verification(unittest.TestCase):
    entry = oa.lookup_app("opencode")[1]

    def test_wrong_app_detected(self):
        before = {1: "explorer.exe"}
        after = {1: "explorer.exe", 50: "cursor.exe"}
        with mock.patch.object(oa, "_process_snapshot", return_value=after), \
             mock.patch.object(oa, "_window_titles", return_value={"main.py - Cursor"}), \
             mock.patch.object(oa.time, "sleep", lambda s: None):
            v, proof = oa.verify_launch("opencode", self.entry, before, set(), 0.1)
        self.assertEqual(v, "wrong")
        self.assertEqual(proof, "Cursor")

    def test_right_process_verified(self):
        with mock.patch.object(oa, "_process_snapshot", return_value={9: "opencode.exe"}), \
             mock.patch.object(oa, "_window_titles", return_value={"opencode"}):
            v, proof = oa.verify_launch("opencode", self.entry, {}, set(), 0.1)
        self.assertEqual(v, "ok")
        self.assertIn("opencode.exe", proof)

    def test_cursor_window_is_not_opencode(self):
        # Judul jendela Cursor yang memuat nama folder "opencode" tidak lolos.
        self.assertFalse(oa._title_ok(self.entry, "opencode-project - Cursor"))

    def test_nothing_appeared(self):
        with mock.patch.object(oa, "_process_snapshot", return_value={1: "x.exe"}), \
             mock.patch.object(oa, "_window_titles", return_value={"Desktop"}), \
             mock.patch.object(oa.time, "sleep", lambda s: None):
            v, _ = oa.verify_launch("opencode", self.entry, {1: "x.exe"}, {"Desktop"}, 0.1)
        self.assertEqual(v, "none")


class WindowsFlow(unittest.TestCase):
    def test_unknown_app_asks_instead_of_guessing(self):
        with mock.patch.object(oa, "_SYSTEM", "Windows"), \
             mock.patch.object(oa, "_resolve_cmd", return_value=""), \
             mock.patch.object(oa, "_launch_windows_legacy") as legacy:
            out = oa.open_app({"app_name": "open code editor thing"})
        legacy.assert_not_called()
        self.assertTrue(out.startswith("Unknown app"))

    def test_wrong_app_reported_honestly(self):
        with mock.patch.object(oa, "_SYSTEM", "Windows"), \
             mock.patch.object(oa, "_try_launch", return_value="C:/x/opencode.cmd"), \
             mock.patch.object(oa, "_process_snapshot",
                               side_effect=[{1: "a.exe"}] + [{1: "a.exe", 2: "cursor.exe"}] * 50), \
             mock.patch.object(oa, "_window_titles", return_value={"x - Cursor"}), \
             mock.patch.object(oa.time, "sleep", lambda s: None):
            out = oa.open_app({"app_name": "OpenCode"})
        self.assertTrue(out.startswith("A different app opened: Cursor"), out)
        self.assertNotIn("Opened OpenCode", out)


if __name__ == "__main__":
    unittest.main()
