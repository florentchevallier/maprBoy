import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from wahoondrer import home

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


class HomeTest(unittest.TestCase):
    def test_maprboy_home_or_the_old_folder(self):
        with mock.patch.dict(os.environ, {"MAPRBOY_HOME": "/x/maprBoy-app/state"}):
            self.assertEqual(home.path("settings.json"), "/x/maprBoy-app/state/settings.json")
        with mock.patch.dict(os.environ, {"MAPRBOY_HOME": ""}):
            self.assertEqual(home.app_home(), os.path.join(os.path.expanduser("~"), ".wahoondrer"))


class InstallSetupTest(unittest.TestCase):
    """scripts/install_setup.py in a fresh process, as the installers run it: MAPRBOY_HOME and HOME in a temp folder."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = dict(os.environ, HOME=os.path.join(self.tmp, "home"), MAPRBOY_HOME=os.path.join(self.tmp, "state"))
        os.makedirs(self.env["HOME"])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_setup(self, *extra):
        data = os.path.join(self.tmp, "My data")
        subprocess.run([sys.executable, os.path.join(REPO, "scripts", "install_setup.py"), "--data", data, *extra],
                       env=self.env, check=True, capture_output=True, text=True)
        with open(os.path.join(self.env["MAPRBOY_HOME"], "settings.json")) as f:
            return data, json.load(f)

    def test_fills_only_what_is_empty(self):
        data, s = self.run_setup("--mkgmap", "/t/mkgmap.jar", "--adb", "/t/adb")
        self.assertEqual(s["folders"]["out"], os.path.join(data, "Output"))
        self.assertTrue(os.path.isdir(os.path.join(data, "Wandrer_exports")))
        link = os.path.join(self.env["HOME"], "wahooMapsCreatorData")
        self.assertEqual(os.path.realpath(link), os.path.realpath(os.path.join(data, "OSM_data")))
        self.assertEqual((s["tools"]["mkgmap"], s["tools"]["adb"], s["tools"]["osmosis"]), ("/t/mkgmap.jar", "/t/adb", ""))
        self.assertFalse(os.path.exists(os.path.join(self.env["HOME"], ".wahoondrer")))      # nothing in the old place
        # an update: the user's choices stay
        s["tools"]["mkgmap"] = "/mine/mkgmap.jar"
        s["folders"]["out"] = "/mine/out"
        with open(os.path.join(self.env["MAPRBOY_HOME"], "settings.json"), "w") as f:
            json.dump(s, f)
        _, s2 = self.run_setup("--mkgmap", "/t/mkgmap.jar", "--osmosis", "/t/osmosis")
        self.assertEqual((s2["tools"]["mkgmap"], s2["folders"]["out"], s2["tools"]["osmosis"]),
                         ("/mine/mkgmap.jar", "/mine/out", "/t/osmosis"))

    def test_existing_wmc_data_left_alone(self):
        wmc = os.path.join(self.env["HOME"], "wahooMapsCreatorData")
        os.makedirs(wmc)
        self.run_setup()
        self.assertTrue(os.path.isdir(wmc) and not os.path.islink(wmc))


if __name__ == "__main__":
    unittest.main()
