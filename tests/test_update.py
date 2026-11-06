import os
import shutil
import tempfile
import unittest
from unittest import mock

from wahoondrer import update


class VersionTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(update.parse("v1.2"), (1, 2))
        self.assertEqual(update.parse("1.10.3"), (1, 10, 3))
        self.assertIsNone(update.parse("main"))
        self.assertIsNone(update.parse("land-polygons"))
        self.assertIsNone(update.parse(None))

    def test_is_newer(self):
        self.assertTrue(update.is_newer("v1.1", "v1.0"))
        self.assertTrue(update.is_newer("v1.10", "v1.9"))       # numbers, not text
        self.assertFalse(update.is_newer("v1.0", "v1.0"))
        self.assertFalse(update.is_newer("v1.0", "v1.0.0"))
        self.assertFalse(update.is_newer("v1.0", "v1.1"))
        self.assertTrue(update.is_newer("v1.0", "main"))         # installed before the first release
        self.assertTrue(update.is_newer("v1.0", "copy"))         # installed from a downloaded copy
        self.assertFalse(update.is_newer("v1.0", None))          # a development copy
        self.assertFalse(update.is_newer(None, "v1.0"))          # no release published


class Done:
    """A terminal program that ended well at once."""
    def wait(self, timeout=None):
        return 0


class CheckTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"MAPRBOY_HOME": os.path.join(self.dir, "maprBoy-app", "state")})
        self.env.start()
        self.calls = 0

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def fetch(self):
        self.calls += 1
        return {"tag": "v1.1", "url": "https://github.com/x/releases/tag/v1.1"}

    def test_once_a_day_unless_forced(self):
        self.assertEqual(update.latest(now=1000, fetch=self.fetch)["tag"], "v1.1")
        update.latest(now=1000 + 3600, fetch=self.fetch)
        self.assertEqual(self.calls, 1)
        update.latest(now=1000 + 3600, fetch=self.fetch, force=True)
        self.assertEqual(self.calls, 2)
        update.latest(now=1000 + 3600 + update.DAY, fetch=self.fetch)
        self.assertEqual(self.calls, 3)

    def test_offline_keeps_the_last_answer(self):
        update.latest(now=1000, fetch=self.fetch)

        def offline():
            raise OSError("no network")
        info = update.latest(now=1000 + update.DAY, fetch=offline)
        self.assertEqual(info["tag"], "v1.1")
        self.assertIn("no network", info["error"])
        self.assertEqual(update.latest(now=1000 + update.DAY + 60, fetch=self.fetch)["error"], "no network")  # not retried

    def make_install(self, version):
        code = os.path.join(self.dir, "maprBoy-app", "code")
        os.makedirs(os.path.join(code, "scripts"))
        open(os.path.join(code, "scripts", "gui.py"), "w").close()
        if version:
            with open(os.path.join(code, "VERSION"), "w") as f:
                f.write(version + "\n")
        return code

    def test_status_of_an_installed_copy(self):
        code = self.make_install("v1.0")
        self.assertEqual(update.install_dir(), self.dir)
        st = update.status(code_dir=code, now=5000, fetch=self.fetch)
        self.assertEqual((st["current"], st["latest"], st["newer"], st["can_update"]), ("v1.0", "v1.1", True, True))
        off = update.status(enabled=False, code_dir=code, fetch=self.fetch)
        self.assertNotIn("latest", off)
        self.assertEqual(update.status(enabled=False, force=True, code_dir=code, fetch=self.fetch)["latest"], "v1.1")

    def test_development_copy(self):
        code = self.make_install(None)
        st = update.status(code_dir=code, fetch=self.fetch)
        self.assertIsNone(st["current"])
        self.assertNotIn("newer", st)
        self.assertEqual(self.calls, 0)                           # GitHub is not even asked
        with mock.patch.dict(os.environ, {"MAPRBOY_HOME": os.path.join(self.dir, "elsewhere")}):
            self.assertIsNone(update.install_dir())

    def test_start_update_on_linux(self):
        self.make_install("v1.0")
        started = []
        problem = update.start_update(system="linux", which=lambda n: "/usr/bin/" + n if n == "gnome-terminal" else None,
                                      popen=lambda cmd, **kw: started.append(cmd) or Done())
        self.assertIsNone(problem)
        script = os.path.join(self.dir, "maprBoy-app", "state", "update.sh")
        self.assertEqual(started, [["gnome-terminal", "--", "bash", script]])
        with open(script) as f:
            text = f.read()
        self.assertIn(f"--dir {self.dir} --update", text)
        self.assertIn(f"exec bash {os.path.join(self.dir, 'maprBoy-app', 'maprBoy.sh')}", text)
        self.assertIn("No terminal", update.start_update(system="linux", which=lambda n: None, popen=lambda c, **k: None))

    def test_a_terminal_that_fails_at_once_is_skipped(self):
        self.make_install("v1.0")
        started = []

        class Proc:
            def __init__(self, cmd):
                started.append(cmd[0])
                self.rc = 1 if cmd[0] == "ptyxis" else 0

            def wait(self, timeout=None):
                return self.rc
        update.start_update(system="linux", which=lambda n: n in ("ptyxis", "x-terminal-emulator") and n,
                            popen=lambda cmd, **kw: Proc(cmd))
        self.assertEqual(started, ["ptyxis", "x-terminal-emulator"])

    def test_not_installed(self):
        self.assertIn("not installed", update.start_update(system="linux"))


class ScriptTest(unittest.TestCase):
    def test_folder_names_are_quoted(self):
        mac = update.script_text("/Users/a b/it's", "darwin")
        self.assertIn("--dir '/Users/a b/it'\"'\"'s' --update", mac)
        self.assertIn("maprBoy.command", mac)
        win = update.script_text("C:\\Users\\O'Neil\\maprBoy", "win32")
        self.assertIn("$dir = 'C:\\Users\\O''Neil\\maprBoy'", win)
        self.assertIn("-Dir $dir -Update", win)
        self.assertIn("maprBoy-app\\maprBoy.bat", win)


if __name__ == "__main__":
    unittest.main()
