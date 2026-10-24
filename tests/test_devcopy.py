"""Automatic copy to a Wahoo ELEMNT with adb (wahoondrer/devcopy.py), with a fake device."""
import hashlib
import json
import os
import tempfile
import unittest
from unittest import mock

from wahoondrer import devcopy, roam

LS = """total 141204
-rw-rw---- 1 root sdcard_rw 20053243 2026-10-08 21:47 87.map.lzma
-rw-rw---- 1 root sdcard_rw        0 2026-10-08 21:47 87.map.lzma.17
-rw-rw---- 1 root sdcard_rw      125 2026-10-08 21:47 87_Regensburg.txt
drwxrwx--x 2 root sdcard_rw     3488 2026-10-08 20:10 icons
"""


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def make_out(d, roam_files=False):
    m = os.path.join(d, "wahoondrer_maps", "maps")
    _write(os.path.join(m, "vtm-elemnt", "vtm-elemnt.xml"), b"<theme/>")
    _write(os.path.join(m, "vtm-elemnt", "icons", "cafe.svg"), b"<svg/>")
    _write(os.path.join(m, "vtm-elemnt", "icons", ".DS_Store"), b"x")
    _write(os.path.join(m, "tiles", "8", "136", "88.map.lzma"), b"T" * 100)
    _write(os.path.join(m, "tiles", "8", "136", "88.map.lzma.17"), b"")
    _write(os.path.join(m, "tiles", "8", "136", "88_Munich.txt"), b"x")
    _write(os.path.join(m, "tiles", "8", "136", "89.map.lzma"), b"old")          # not of the last run
    if roam_files:
        r = os.path.join(d, roam.DIR)
        _write(os.path.join(r, roam.CFG), b"")
        _write(os.path.join(r, "maps", "vtm-elemnt", "vtm-elemnt.xml"), b"<theme/>")
        _write(os.path.join(r, "maps", "tiles", "8", "136", "88.map.gz"), b"G" * 80)
    with open(os.path.join(d, "last_run.json"), "w") as f:
        json.dump({"selected": ["136/88"], "built": ["136/88"]}, f)


class FakeDevice:
    """Files of the device {remote path: bytes}; records the adb calls."""
    def __init__(self, files=None):
        self.files, self.calls = dict(files or {}), []

    def run(self, *args, check=True, timeout=600):
        self.calls.append(args)
        if args[0] == "push":
            with open(args[1], "rb") as f:
                self.files[args[2]] = f.read()
        elif args[0] == "pull":
            _write(args[2], self.files[args[1]])
        elif args[0] == "shell":
            return self.shell_cmd(args[1])
        return ""

    def shell_cmd(self, cmd):
        if cmd.startswith("ls -l "):
            folder = cmd.split()[2]
            rows = [f"-rw-rw---- 1 root sdcard_rw {len(v)} 2026-10-24 13:16 {k.rsplit('/', 1)[1]}"
                    for k, v in self.files.items() if k.rsplit("/", 1)[0] == folder]
            return "\n".join(rows)
        if cmd.startswith("md5sum "):
            path = cmd.split()[1]
            return hashlib.md5(self.files[path]).hexdigest() + "  " + path if path in self.files else ""
        if cmd.startswith("mv -f "):
            src, dst = cmd.split()[2:4]
            self.files[dst] = self.files.pop(src)
        if cmd.startswith("am broadcast"):
            return "Broadcast completed: result=0"
        return ""

    def sizes(self, folder):
        return devcopy.parse_ls(self.shell_cmd("ls -l " + folder))

    def shell(self, cmd, check=True):
        return self.run("shell", cmd, check=check)


class DevCopyTest(unittest.TestCase):
    def test_parse_ls(self):
        self.assertEqual(devcopy.parse_ls(LS), {"87.map.lzma": 20053243, "87.map.lzma.17": 0, "87_Regensburg.txt": 125})

    def test_plan_bolt_and_roam(self):
        with tempfile.TemporaryDirectory() as d:
            make_out(d, roam_files=True)
            rem = [r for _, r in devcopy.plan(d)]
            self.assertEqual(rem, ["/sdcard/maps/vtm-elemnt/vtm-elemnt.xml", "/sdcard/maps/vtm-elemnt/icons/cafe.svg",
                                   "/sdcard/maps/tiles/8/136/88.map.lzma", "/sdcard/maps/tiles/8/136/88.map.lzma.17",
                                   "/sdcard/maps/tiles/8/136/88_Munich.txt"])
            rem = [r for _, r in devcopy.plan(d, "roam1")]
            self.assertEqual(rem, ["/sdcard/" + roam.CFG, "/sdcard/maps/vtm-elemnt/vtm-elemnt.xml",
                                   "/sdcard/maps/tiles/8/136/88.map.gz", "/sdcard/maps/tiles/8/136/88_Munich.txt"])

    def test_plan_needs_a_run(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(devcopy.CopyError):
                devcopy.plan(d)
            make_out(d)
            with self.assertRaises(devcopy.CopyError):                     # no roam_v1 files yet
                devcopy.plan(d, "roam1")

    def _copy(self, d, dev):
        with mock.patch.object(devcopy, "find_adb", return_value="/x/adb"), \
                mock.patch.object(devcopy, "devices", return_value=[{"serial": "S1", "state": "device"}]), \
                mock.patch.object(devcopy, "Adb", return_value=dev):
            return devcopy.copy(d, log=lambda *_: None)

    def test_copy_skips_same_files_saves_replaced_ones_and_reloads(self):
        with tempfile.TemporaryDirectory() as d:
            make_out(d)
            dev = FakeDevice({"/sdcard/maps/vtm-elemnt/vtm-elemnt.xml": b"<theme/>",       # same: skipped
                              "/sdcard/maps/tiles/8/136/88.map.lzma": b"OLDTILE"})          # replaced: saved first
            r = self._copy(d, dev)
            self.assertEqual((r["copied"], r["skipped"], r["saved"]), (4, 1, 1))
            self.assertEqual(dev.files["/sdcard/maps/tiles/8/136/88.map.lzma"], b"T" * 100)
            with open(os.path.join(r["backup"], "maps", "tiles", "8", "136", "88.map.lzma"), "rb") as f:
                self.assertEqual(f.read(), b"OLDTILE")
            self.assertFalse(any(k.endswith(".part") for k in dev.files))
            pushes = [c for c in dev.calls if c[0] == "push"]
            self.assertTrue(all(c[2].endswith(".part") for c in pushes))                 # never half a tile
            shells = [c[1] for c in dev.calls if c[0] == "shell"]
            self.assertIn("rm -rf /sdcard/maps/temp/*", shells)
            self.assertFalse(any(s.startswith("am broadcast") for s in shells))       # did not reload the map: restart instead
            self.assertNotIn("/sdcard/maps/tiles/8/136/89.map.lzma", dev.files)       # not of the last run

    def test_nothing_new_means_no_reload(self):
        with tempfile.TemporaryDirectory() as d:
            make_out(d)
            dev = FakeDevice()
            self._copy(d, dev)
            dev.calls.clear()
            r = self._copy(d, dev)
            self.assertEqual((r["copied"], r["saved"]), (0, 0))
            self.assertFalse(any(c[0] == "push" for c in dev.calls))
            self.assertFalse(any(c[0] == "shell" and c[1].startswith(("rm ", "am ")) for c in dev.calls))

    def test_reboot(self):
        dev = FakeDevice()
        with mock.patch.object(devcopy, "find_adb", return_value="/x/adb"), \
                mock.patch.object(devcopy, "devices", return_value=[{"serial": "S1", "state": "device"}]), \
                mock.patch.object(devcopy, "Adb", return_value=dev) as adb:
            self.assertEqual(devcopy.reboot(), "S1")
        adb.assert_called_with("/x/adb", "S1")
        self.assertEqual(dev.calls, [("reboot",)])
        with mock.patch.object(devcopy, "find_adb", return_value="/x/adb"), \
                mock.patch.object(devcopy, "devices", return_value=[{"serial": "S1", "state": "unauthorized"}]):
            with self.assertRaises(devcopy.CopyError):
                devcopy.reboot()

    def test_status_without_adb(self):
        with mock.patch.object(devcopy, "find_adb", return_value=None):
            self.assertEqual(devcopy.status(), {"adb": None, "devices": [], "ready": False})
        with mock.patch.object(devcopy, "find_adb", return_value="/x/adb"), \
                mock.patch.object(devcopy, "devices", return_value=[{"serial": "S1", "state": "unauthorized"}]):
            self.assertFalse(devcopy.status()["ready"])


if __name__ == "__main__":
    unittest.main()
