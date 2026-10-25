import tempfile
import unittest
from pathlib import Path

from tests._util import no_audit  # noqa: F401
from actions import code_helper as ch


class Replace(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.f = Path(self.dir.name) / "app.py"
        self.f.write_text("a = 1\nb = 2\nb = 2\n", encoding="utf-8")

    def tearDown(self):
        self.dir.cleanup()

    def test_unique_replace_verified_and_undoable(self):
        from core import undo
        out = ch._replace_action(str(self.f), "a = 1", "a = 10")
        self.assertIn("verified", out)
        self.assertEqual(self.f.read_text(encoding="utf-8").splitlines()[0], "a = 10")
        undo.undo_last()
        self.assertEqual(self.f.read_text(encoding="utf-8").splitlines()[0], "a = 1")

    def test_ambiguous_refused(self):
        out = ch._replace_action(str(self.f), "b = 2", "b = 3")
        self.assertIn("2 times", out)

    def test_secret_files_refused(self):
        self.assertTrue(ch._replace_action(str(Path(self.dir.name) / "api_keys.json"),
                                           "", "x").startswith("Refused"))
        self.assertTrue(ch._read_lines_action(str(Path(self.dir.name) / "x.pem")).startswith(
            ("Refused", "File not found")))

    def test_read_numbered(self):
        out = ch._read_lines_action(str(self.f), 2, 1)
        self.assertIn("    2| b = 2", out)


if __name__ == "__main__":
    unittest.main()
