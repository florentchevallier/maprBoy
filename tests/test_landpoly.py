import os
import shutil
import tempfile
import time
import unittest
import zipfile

from wahoondrer import landpoly

DAY = 86400


class LandPolygonsTest(unittest.TestCase):
    def setUp(self):
        self.dl = tempfile.mkdtemp()
        self.folder = os.path.join(self.dl, landpoly.FOLDER)
        self.calls = []

    def tearDown(self):
        shutil.rmtree(self.dl, ignore_errors=True)

    def old_copy(self, days, text="old"):
        os.makedirs(self.folder, exist_ok=True)
        shp = os.path.join(self.folder, landpoly.SHP)
        with open(shp, "w") as f:
            f.write(text)
        t = time.time() - days * DAY
        os.utime(shp, (t, t))
        return shp

    def good_download(self, url, path):
        self.calls.append(url)
        with zipfile.ZipFile(path, "w") as z:
            z.writestr(f"{landpoly.FOLDER}/{landpoly.SHP}", "new")
            z.writestr(f"{landpoly.FOLDER}/land_polygons.dbf", "new")

    def no_network(self, url, path):
        self.calls.append(url)
        raise ConnectionError("HTTPSConnectionPool(host='osmdata.openstreetmap.de', port=443): Max retries exceeded")

    def content(self):
        with open(os.path.join(self.folder, landpoly.SHP)) as f:
            return f.read()

    def test_a_recent_copy_is_kept_and_looks_new_to_wmc(self):
        shp = self.old_copy(30)
        self.assertEqual(landpoly.refresh(self.dl, self.good_download, log=lambda *a: None), "fresh")
        self.assertEqual(self.calls, [])
        self.assertLess(time.time() - os.path.getmtime(shp), 60)          # WMC (14 days) will not delete it
        stamp = os.path.getmtime(os.path.join(self.folder, landpoly.STAMP))
        self.assertAlmostEqual(time.time() - stamp, 30 * DAY, delta=60)  # but its real date is remembered
        # 200 days later: renewed, although the shp was touched in between
        self.assertEqual(landpoly.refresh(self.dl, self.good_download, log=lambda *a: None, now=time.time() + 170 * DAY),
                         "renewed")
        self.assertEqual(self.content(), "new")
        self.assertEqual(sorted(os.listdir(self.dl)), [landpoly.FOLDER])   # no zip, no .old, no .new left

    def test_server_down_keeps_the_old_copy(self):
        self.old_copy(400)
        lines = []
        self.assertEqual(landpoly.refresh(self.dl, self.no_network, log=lines.append), "kept")
        self.assertEqual(self.content(), "old")
        self.assertIn("old copy is used", lines[-1])
        self.assertEqual(sorted(os.listdir(self.dl)), [landpoly.FOLDER])
        self.assertLess(time.time() - os.path.getmtime(os.path.join(self.folder, landpoly.SHP)), 60)

    def test_no_copy_and_no_server(self):
        with self.assertRaises(landpoly.LandError) as ctx:
            landpoly.refresh(self.dl, self.no_network, log=lambda *a: None)
        self.assertIn("osmdata.openstreetmap.de", str(ctx.exception))
        self.assertEqual(os.listdir(self.dl), [])

    def test_http_error_of_the_progress_download(self):
        self.old_copy(400)

        def http_error(url, path):
            raise SystemExit(1)                    # what progress.download does on a status other than 200
        self.assertEqual(landpoly.refresh(self.dl, http_error, log=lambda *a: None), "kept")

    def test_the_copy_on_github_when_the_official_site_is_down(self):
        def official_down(url, path):
            self.calls.append(url)
            if "osmdata.openstreetmap.de" in url:
                with open(path, "w") as f:
                    f.write("half")                  # a partial file must not stay
                raise ConnectionError("Max retries exceeded")
            self.good_download(url, path)
        lines = []
        self.assertEqual(landpoly.refresh(self.dl, official_down, log=lines.append), "renewed")
        self.assertEqual(self.calls, [landpoly.URL, landpoly.MIRROR, landpoly.MIRROR])
        self.assertTrue(any("trying the copy on github.com" in l for l in lines))
        self.assertEqual(self.content(), "new")
        self.assertEqual(sorted(os.listdir(self.dl)), [landpoly.FOLDER])

    def test_first_download(self):
        self.assertEqual(landpoly.refresh(self.dl, self.good_download, log=lambda *a: None), "renewed")
        self.assertEqual(self.content(), "new")
        self.assertTrue(os.path.isfile(os.path.join(self.folder, "land_polygons.dbf")))


if __name__ == "__main__":
    unittest.main()
