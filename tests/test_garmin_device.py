import os
import shutil
import tempfile
import unittest
from unittest import mock

from wahoondrer.garmin import device

DETECT = """libmtp version: 1.1.23

Listing raw device(s)
Device 0 (VID=091e and PID=4fb8) is a Garmin Forerunner 955 Solar.
   Found 1 device(s):
"""
FOLDERS = "Friendly name: Forerunner 955\nStorage: Internal Storage\n16777216\tGARMIN\n16777229\t  Audio\n16777364\t    Tones\n"
FILES = """File ID: 16779431
   Filename: maprboy-old.img
   File size 5 (0x0000000000000005) bytes
   Parent ID: 16777216
   Storage ID: 0x00020001
File ID: 16779500
   Filename: maprboy-same.img
   File size 4 (0x0000000000000004) bytes
   Parent ID: 16777216
File ID: 16779600
   Filename: maprboy-old.img
   File size 5 (0x0000000000000005) bytes
   Parent ID: 16777229
"""


class ParseTest(unittest.TestCase):
    def test_outputs_of_the_libmtp_tools(self):
        self.assertEqual(device.parse_detect(DETECT), ["Forerunner 955 Solar"])          # libmtp's list only
        info = DETECT + "Device info:\n   Manufacturer: Garmin\n   Model: Forerunner 955\n   Device version: 2905\n"
        self.assertEqual(device.parse_detect(info), ["Forerunner 955"])                  # the watch's own name
        self.assertEqual(device.parse_folders(FOLDERS), {"GARMIN": 16777216})            # top folders only
        files = device.parse_files(FILES)
        self.assertEqual(files[0], {"id": 16779431, "name": "maprboy-old.img", "size": 5, "parent": 16777216})
        self.assertEqual(len(files), 3)


class VersionTest(unittest.TestCase):
    def test_older_versions(self):
        there = ["maprboy-Munich-2026-10-28-base.img", "maprboy-Munich-2026-10-28-overlay.img",
                 "maprboy-Munich-foot-2026-10-30-overlay.img", "maprboy-Munich-bike-2026-10-29-overlay.img",
                 "maprboy-Munich-2026-12-01-base.img", "maprboy-Athens-2026-10-28-base.img", "maprboy-base.img",
                 "maprboy-Moosburg-an-der-Isar-2026-10-01-base.img", "gmapsupp.img"]
        self.assertEqual(device.older_versions("maprboy-Munich-2026-11-04-base.img", there),
                         ["maprboy-Munich-2026-10-28-base.img"])                  # not the newer one, not Athens
        self.assertEqual(device.older_versions("maprboy-Munich-bike-2026-11-04-overlay.img", there),
                         ["maprboy-Munich-bike-2026-10-29-overlay.img"])          # the same activity only
        self.assertEqual(device.older_versions("maprboy-Moosburg-an-der-Isar-2026-11-04-base.img", there),
                         ["maprboy-Moosburg-an-der-Isar-2026-10-01-base.img"])
        self.assertEqual(device.older_versions("maprboy-base.img", there), [])     # no date: no versions


class DiskTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.vol = os.path.join(self.tmp, "Volumes", "GARMIN")
        os.makedirs(os.path.join(self.vol, "Garmin"))
        with open(os.path.join(self.vol, "Garmin", "GarminDevice.xml"), "w") as f:
            f.write("<Device><Model><PartNumber>006-B3122-00</PartNumber><Description>Edge 830</Description></Model></Device>")
        os.makedirs(os.path.join(self.tmp, "Volumes", "Macintosh HD"))
        self.maps = []
        for name, text in (("a-overlay.img", "AAAA"), ("a-base.img", "BB")):
            p = os.path.join(self.tmp, name)
            with open(p, "w") as f:
                f.write(text)
            self.maps.append(p)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_found_by_its_model_and_copied_once(self):
        self.assertEqual(device.disks(os.path.join(self.tmp, "Volumes")),
                         [{"kind": "disk", "name": "Edge 830", "path": self.vol}])
        lines = []
        self.assertEqual(device.copy_to_disk(self.maps, self.vol, log=lines.append), 2)
        with open(os.path.join(self.vol, "Garmin", "a-overlay.img")) as f:
            self.assertEqual(f.read(), "AAAA")
        self.assertEqual(device.copy_to_disk(self.maps, self.vol, log=lines.append), 0)   # already there
        self.assertFalse([n for n in os.listdir(os.path.join(self.vol, "Garmin")) if n.endswith(".part")])


    def test_older_versions_deleted_when_asked(self):
        g = os.path.join(self.vol, "Garmin")
        for n in ("maprboy-M-2026-10-28-base.img", "maprboy-M-2026-10-29-base.img", "maprboy-M-foot-2026-10-28-overlay.img"):
            with open(os.path.join(g, n), "w") as f:
                f.write("old")
        new = os.path.join(self.tmp, "maprboy-M-2026-11-04-base.img")
        older_sent = os.path.join(self.tmp, "maprboy-M-2026-10-29-base.img")
        for p in (new, older_sent):
            with open(p, "w") as f:
                f.write("new")
        device.copy_to_disk([new], self.vol, log=lambda *a: None)
        self.assertEqual(len([n for n in os.listdir(g) if n.startswith("maprboy-M-")]), 4)    # not asked: all kept
        device.copy_to_disk([new, older_sent], self.vol, log=lambda *a: None, replace_older=True)
        self.assertEqual(sorted(n for n in os.listdir(g) if n.startswith("maprboy-M-")),
                         ["maprboy-M-2026-10-29-base.img", "maprboy-M-2026-11-04-base.img",   # one sent now: kept
                          "maprboy-M-foot-2026-10-28-overlay.img"])                            # another kind


class MtpTest(unittest.TestCase):
    def test_older_map_deleted_first_same_one_skipped(self):
        tmp = tempfile.mkdtemp()
        try:
            new, same = os.path.join(tmp, "maprboy-old.img"), os.path.join(tmp, "maprboy-same.img")
            with open(new, "w") as f:
                f.write("NEW CONTENT")
            with open(same, "w") as f:
                f.write("SAME")
            calls = []

            def fake(args, what, timeout=3600):
                calls.append(args)
                return {"mtp-folders": FOLDERS, "mtp-files": FILES}.get(args[0], "New file ID: 1\n")
            with mock.patch.object(device, "_mtp", fake), mock.patch.object(device, "mtp_ready", lambda: True):
                self.assertEqual(device.copy_mtp([new, same], log=lambda *a: None), 1)
            self.assertIn(["mtp-delfile", "-n", "16779431"], calls)         # the one in GARMIN, not in Audio
            self.assertNotIn(["mtp-delfile", "-n", "16779600"], calls)
            self.assertIn(["mtp-sendfile", new, "16777216/maprboy-old.img"], calls)
            self.assertFalse([c for c in calls if c[0] == "mtp-sendfile" and c[1] == same])
            self.assertLess(calls.index(["mtp-delfile", "-n", "16779431"]),
                            calls.index(["mtp-sendfile", new, "16777216/maprboy-old.img"]))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_older_versions_deleted_when_asked(self):
        tmp = tempfile.mkdtemp()
        try:
            new = os.path.join(tmp, "maprboy-M-2026-11-04-base.img")
            with open(new, "w") as f:
                f.write("NEW")
            listing = FILES + ("File ID: 16779700\n   Filename: maprboy-M-2026-10-28-base.img\n"
                               "   File size 9 (0x9) bytes\n   Parent ID: 16777216\n")
            calls = []

            def fake(args, what, timeout=3600):
                calls.append(args)
                return {"mtp-folders": FOLDERS, "mtp-files": listing}.get(args[0], "New file ID: 1\n")
            with mock.patch.object(device, "_mtp", fake), mock.patch.object(device, "mtp_ready", lambda: True):
                device.copy_mtp([new], log=lambda *a: None)
                self.assertFalse([c for c in calls if c[0] == "mtp-delfile"])
                device.copy_mtp([new], log=lambda *a: None, replace_older=True)
            self.assertEqual([c for c in calls if c[0] == "mtp-delfile"], [["mtp-delfile", "-n", "16779700"]])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_windows_copy_lists_the_older_versions(self):
        class FakeProc:
            def __init__(self, *a, **k):
                self.stdout = iter(["STEP|1|1|maprboy-M-2026-11-04-base.img\n", "  copied maprboy-M-2026-11-04-base.img (0.1 MB)\n",
                                    "1 map(s) sent to the GARMIN folder of Forerunner 955.\n"])

            def wait(self):
                return 0
        lines = []
        listing = {"devices": ["Forerunner 955"], "maps": ["maprboy-M-2026-10-28-base.img", "maprboy-M-2026-11-04-base.img"]}
        with mock.patch.object(device.subprocess, "Popen", FakeProc), mock.patch.object(device, "win_list", lambda: listing):
            device.copy_mtp_windows(["/x/maprboy-M-2026-11-04-base.img"], log=lines.append, replace_older=True)
        self.assertIn("  copied maprboy-M-2026-11-04-base.img (0.1 MB)", lines)
        self.assertTrue(lines[-1].startswith("! Older versions on the watch") and "maprboy-M-2026-10-28-base.img" in lines[-1])

    def test_windows_copy_error(self):
        class FakeProc:
            def __init__(self, *a, **k):
                self.stdout = iter(["Error: no Garmin watch found\n"])

            def wait(self):
                return 1
        with mock.patch.object(device.subprocess, "Popen", FakeProc):
            with self.assertRaises(device.DeviceError) as ctx:
                device.copy_mtp_windows(["/x/a.img"], log=lambda *a: None)
        self.assertIn("no Garmin watch found", str(ctx.exception))

    def test_mark(self):
        tmp = tempfile.mkdtemp()
        try:
            device.mark(tmp, ["/x/maprboy-a-overlay.img", "maprboy-a-base.img"])
            import json
            with open(os.path.join(tmp, "last_run.json")) as f:
                self.assertEqual(json.load(f), {"maps": ["maprboy-a-overlay.img", "maprboy-a-base.img"]})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
