import os
import shutil
import stat
import tempfile
import unittest

from wahoondrer import contour

TILE = {"left": -4.5, "bottom": 47.7, "right": -4.2, "top": 47.9}


class SourcesOptionTest(unittest.TestCase):
    """pyhgtmap 4.x names the option --sources, pyhgtmap 3.9 (what older Macs get) --source."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def fake(self, name, help_text):
        path = os.path.join(self.dir, name)
        with open(path, "w") as f:
            f.write("#!/bin/sh\ncat <<'EOF'\n" + help_text + "\nEOF\n")
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        return path

    @unittest.skipIf(os.name == "nt", "shell scripts as fake programs")
    def test_old_and_new_pyhgtmap(self):
        old = self.fake("pyhgtmap39", "good results can be achieved by specifying --source=view3.\n"
                                      "  --source DATA_SOURCE, --data-source DATA_SOURCE")
        new = self.fake("pyhgtmap41", "  --sources DATA_SOURCES, --data-sources DATA_SOURCES\n"
                                      "Specifying --sources=view3,srtm3 for example")
        self.assertEqual(contour.sources_option(old), "--source")
        self.assertEqual(contour.sources_option(new), "--sources")
        cmd = contour.build_command(old, TILE, "/out/e", "/hgt", sources_flag=contour.sources_option(old))
        self.assertIn(f"--source={contour.SOURCES}", cmd)
        self.assertIn("--area=-4.5:47.7:-4.2:47.9", cmd)

    def test_unknown_program_keeps_the_current_name(self):
        self.assertEqual(contour.sources_option(os.path.join(self.dir, "missing")), "--sources")
        self.assertIn(f"--sources={contour.SOURCES}", contour.build_command("x", TILE, "/out/e", "/hgt"))


if __name__ == "__main__":
    unittest.main()
