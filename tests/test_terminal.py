import sys
import unittest

from tests._util import no_audit  # noqa: F401
from actions import terminal


class Terminal(unittest.TestCase):
    def test_command_is_single_argument(self):
        argv = terminal.build_argv('echo "a & b"; rm x', "powershell")
        self.assertEqual(argv[-1], 'echo "a & b"; rm x')
        self.assertEqual(argv[:2], ["powershell", "-NoProfile"])
        self.assertEqual(terminal.build_argv("dir", "cmd")[-1], "dir")

    @unittest.skipIf(sys.platform.startswith("win"), "uji bash")
    def test_runs_and_reports_exit_code(self):
        out = terminal.run_command({"command": "echo halo; exit 3", "cwd": "/tmp", "shell": "bash"})
        self.assertIn("exit_code: 3", out)
        self.assertIn("halo", out)

    def test_bad_cwd(self):
        out = terminal.run_command({"command": "echo x", "cwd": "/no/such/dir/x"})
        self.assertTrue(out.startswith("ERROR"))


if __name__ == "__main__":
    unittest.main()
