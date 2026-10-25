import glob
import os
import re
import tempfile
import unittest
from unittest import mock

from tests import synthetic
import json
import time

import sys

from wahoondrer.gui import jobs, native, regions, scan, server, settings


def _write(path, data, mode="w"):
    with open(path, mode) as f:
        f.write(data)


class ReuseTests(unittest.TestCase):
    def sig(self, **kw):
        from wahoondrer import reuse
        tiles = [{"x": 136, "y": 88, "countries": ["germany/bayern"]}]
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "tags.xml")
            _write(cfg, kw.get("cfg", "a"))
            dl = os.path.join(d, "dl")
            os.makedirs(dl)
            _write(os.path.join(dl, "bayern-latest.osm.pbf"), kw.get("osm", "x"))
            os.utime(os.path.join(dl, "bayern-latest.osm.pbf"), (1000, 1000 + len(kw.get("osm", "x"))))
            return reuse.signature(tiles + kw.get("more", []), kw.get("contour", True), "25-75-150",
                                   kw.get("per_tile", {}), [cfg], dl)

    def test_reuse_only_when_nothing_changed(self):
        from wahoondrer import reuse
        old = self.sig()
        self.assertIsNone(reuse.stale_reason(self.sig(), old))
        self.assertIn("no earlier", reuse.stale_reason(self.sig(), None))
        self.assertIn("136/89", reuse.stale_reason(self.sig(more=[{"x": 136, "y": 89, "countries": ["germany/bayern"]}]), old))
        self.assertIn("contour", reuse.stale_reason(self.sig(per_tile={"136/88": "none"}), old))
        self.assertIn("contour", reuse.stale_reason(self.sig(contour=False), old))
        self.assertIn("POI", reuse.stale_reason(self.sig(cfg="b"), old))
        self.assertIn("OpenStreetMap", reuse.stale_reason(self.sig(osm="xyz"), old))


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        scan.CACHE_PATH = os.path.join(self.dir, "cache", "cache.json")   # never touch the real cache
        scan._cache = None
        synthetic.write_wandrer_kmz(os.path.join(self.dir, "a.kmz"))
        synthetic.write_wandrer_kmz(os.path.join(self.dir, "b.kmz"), stamp="20260927-08:00:00",
                                    lon=11.60)

    def tearDown(self):
        self.tmp.cleanup()

    def test_pending_until_read_then_cached_on_disk(self):
        self.assertTrue(all(i.get("pending") for i in scan.scan_wandrer(self.dir)))
        for p in [i["path"] for i in scan.scan_wandrer(self.dir)]:
            scan.describe_export(p)
        self.assertFalse(any(i.get("pending") for i in scan.scan_wandrer(self.dir)))
        scan._cache = None                       # a new session reads the cache file
        self.assertFalse(any(i.get("pending") for i in scan.scan_wandrer(self.dir)))

    def test_hub_and_type(self):
        synthetic.write_wandrer_kmz(os.path.join(self.dir, "c.kmz"), stamp="20260928-08:00:00",
                                    activity="combined", lon=11.0)
        info = scan.describe_export(os.path.join(self.dir, "c.kmz"))
        self.assertEqual(info["activity"], "combined")
        self.assertAlmostEqual(info["hub"][0], 11.025, places=2)

    def test_scan_reads_contents_and_sorts_newest_first(self):
        for p in [i["path"] for i in scan.scan_wandrer(self.dir)]:
            scan.describe_export(p)
        infos = scan.scan_wandrer(self.dir)
        self.assertEqual([i["name"] for i in infos], ["b.kmz", "a.kmz"])
        a = infos[1]
        self.assertEqual((a["traveled"], a["untraveled"], a["unpaved"], a["boundaries"]), (2, 1, 1, 1))
        self.assertEqual(a["activity"], "bike")
        self.assertEqual(a["tiles"], [[136, 88]])

    def test_squadrats_most_recent_by_date_in_name(self):
        for n in ("squadrats-2026-09-01.kml", "squadrats-2026-09-29.kml", "squadrats-2026-08-30.kml"):
            synthetic.write_squadrats_kml(os.path.join(self.dir, n))
        self.assertEqual(scan.squadrats_files(self.dir)[0]["name"], "squadrats-2026-09-29.kml")

    def test_list_dir_hides_hidden_and_files(self):
        os.mkdir(os.path.join(self.dir, "sub")); os.mkdir(os.path.join(self.dir, ".hid"))
        r = scan.list_dir(self.dir)
        self.assertEqual(r["dirs"], ["sub"])


class ActivityTests(unittest.TestCase):
    def infos(self):
        return [{"name": "new", "activity": "combined", "date": "2"}, {"name": "old", "activity": "bike", "date": "1"},
                {"name": "bad", "error": "x"}, {"name": "todo", "pending": True}]

    def test_default_is_the_type_of_the_newest_export(self):
        s = settings.load(os.devnull); infos = self.infos()
        self.assertIsNone(s["activity"])
        self.assertEqual(server.annotate(s, infos), "combined")
        self.assertEqual([i["active"] for i in infos], [True, False, False, False])
        self.assertIn("other type", infos[1]["why"])

    def test_a_chosen_type_is_kept_whatever_the_newest_export(self):
        s = settings.load(os.devnull); s["activity"] = "bike"; infos = self.infos()
        self.assertEqual(server.annotate(s, infos), "bike")
        self.assertEqual([i["active"] for i in infos], [False, True, False, False])

    def test_any_follows_the_wandrer_block_mode(self):
        s = settings.load(os.devnull); s["activity"] = "any"
        s["ui"]["sections"] = {"wandrer": "pro"}                              # only the Wandrer block in Pro
        infos = self.infos()
        self.assertEqual(server.annotate(s, infos), "")
        self.assertEqual([i["active"] for i in infos], [True, True, False, False])
        s["mode"] = "pro"; s["ui"]["sections"] = {"wandrer": "simple"}       # Pro at the top, Simple for Wandrer
        self.assertEqual(server.annotate(s, self.infos()), "combined")

    def test_any_is_pro_only(self):
        s = settings.load(os.devnull); s["activity"] = "any"
        self.assertEqual(server.annotate(s, self.infos()), "combined")      # simple mode: newest type
        s["mode"] = "pro"; s["excluded_exports"] = ["new"]
        infos = self.infos(); self.assertEqual(server.annotate(s, infos), "")
        self.assertEqual([i["active"] for i in infos], [False, True, False, False])

    def test_no_export_gives_bike(self):
        self.assertEqual(server.effective_activity(settings.load(os.devnull), []), "bike")

    def test_old_latest_and_auto_settings_become_unset(self):
        with tempfile.TemporaryDirectory() as d:
            for old in ("auto", "latest"):
                p = os.path.join(d, "s.json"); _write(p, json.dumps({"activity": old}))
                self.assertIsNone(settings.load(p)["activity"])


class DeviceSettingsTests(unittest.TestCase):
    def test_a_first_version_file_becomes_the_wahoo_settings(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            _write(p, json.dumps({"mode": "pro", "activity": "bike", "tiles": ["136/88"],
                                  "folders": {"wandrer": "/w"}, "squadrats": {"enabled": True, "file": "sq.kml"}}))
            s = settings.load(p)
            self.assertEqual((s["device"], s["mode"], s["activity"], s["tiles"]), ("wahoo", "pro", "bike", ["136/88"]))
            self.assertEqual(s["squadrats"]["file"], "sq.kml")
            s["device"] = "garmin"
            g = settings.save(s, p)                       # switching: the Garmin gets its own (default) settings
            self.assertEqual((g["device"], g["activity"], g["tiles"]), ("garmin", None, []))
            self.assertEqual((g["mode"], g["folders"]["wandrer"]), ("pro", "/w"))   # shared
            self.assertEqual(g["squadrats"]["file"], "sq.kml")                     # shared
            self.assertFalse(g["squadrats"]["enabled"])                            # per device
            raw = json.load(open(p))
            self.assertEqual(raw["version"], 2)
            self.assertEqual(raw["devices"]["wahoo"]["tiles"], ["136/88"])

    def test_each_device_keeps_its_own_export_type(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            s = settings.load(p); s["activity"] = "bike"; s = settings.save(s, p)
            s["device"] = "garmin"; s = settings.save(s, p)
            s["activity"] = "foot"; s["geocode"] = False; s = settings.save(s, p)
            s["device"] = "wahoo"; w = settings.save(s, p)
            self.assertEqual((w["activity"], w["geocode"]), ("bike", False))
            w["device"] = "garmin"; g = settings.save(w, p)
            self.assertEqual(g["activity"], "foot")
            self.assertEqual(settings.load(p)["device"], "garmin")

    def test_coming_back_from_a_device_never_saved_keeps_the_first_one(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            s = settings.load(p); s["activity"] = "bike"; s["tiles"] = ["136/88"]; s = settings.save(s, p)
            s["device"] = "garmin"; g = settings.save(s, p)           # Garmin: defaults, nothing saved for it yet
            g["device"] = "wahoo"; w = settings.save(g, p)
            self.assertEqual((w["activity"], w["tiles"]), ("bike", ["136/88"]))

    def test_the_area_mode_is_kept_per_device(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            s = settings.load(p)
            self.assertEqual(s["area"], {"mode": "manual", "km": 10, "regions": []})
            s["area"] = {"mode": "regions", "km": 25, "regions": ["oberbayern"]}; s = settings.save(s, p)
            s["device"] = "garmin"; g = settings.save(s, p)
            self.assertEqual((g["area"]["mode"], g["area"]["regions"]), ("manual", []))
            g["device"] = "wahoo"
            self.assertEqual(settings.save(g, p)["area"], {"mode": "regions", "km": 25, "regions": ["oberbayern"]})

    def test_the_squadrats_file_is_shared(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            s = settings.load(p); s["squadrats"]["file"] = "a.kml"; s = settings.save(s, p)
            s["device"] = "garmin"; g = settings.save(s, p)
            self.assertEqual(g["squadrats"]["file"], "a.kml")
            self.assertNotIn("file", json.load(open(p))["devices"]["wahoo"]["squadrats"])


class SquadratsLookArgsTests(unittest.TestCase):
    def test_only_a_changed_look_is_passed(self):
        s = settings.load(os.devnull)
        self.assertEqual(jobs.look_args(s["wandrer"], s["squadrats"]), [])
        s["squadrats"]["look"]["light"]["big"]["color"] = "#0000FF"
        args = jobs.look_args(s["wandrer"], s["squadrats"])
        self.assertEqual(args[0], "--theme-squadrats")
        self.assertEqual(json.loads(args[1])["light"]["big"]["color"], "#0000FF")
        s["squadrats"]["look"]["dark"]["small"]["width"] = 0
        with self.assertRaises(jobs.SettingsError):
            jobs.look_args(s["wandrer"], s["squadrats"])


class DroppedFileTests(unittest.TestCase):
    def test_export_name(self):
        self.assertEqual(scan.export_file_name("20261021-18:02:11", "München", "foot", ".KMZ"),
                         "wandrer-2026-10-21-Munchen-foot.kmz")
        self.assertEqual(scan.export_file_name("20261021-18:02:11", "", "bike", ".kmz"), "wandrer-2026-10-21-bike.kmz")
        self.assertTrue(scan.export_file_name("", "Bad Tölz / Wolfratshausen", "", ".kml").endswith(
            "-Bad-Tolz-Wolfratshausen.kml"))

    def test_free_name_never_replaces(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(scan.free_name(d, "a.kmz"), "a.kmz")
            _write(os.path.join(d, "a.kmz"), "1"); _write(os.path.join(d, "a-2.kmz"), "2")
            self.assertEqual(scan.free_name(d, "a.kmz"), "a-3.kmz")

    def test_same_content_is_found(self):
        with tempfile.TemporaryDirectory() as d:
            _write(os.path.join(d, "old.kmz"), "same"); _write(os.path.join(d, "other.kmz"), "diff")
            _write(os.path.join(d, ".upload.kmz"), "same")
            self.assertEqual(scan.same_file_in(d, os.path.join(d, ".upload.kmz"), (".kmz",)), "old.kmz")
            _write(os.path.join(d, ".upload.kmz"), "new!")
            self.assertIsNone(scan.same_file_in(d, os.path.join(d, ".upload.kmz"), (".kmz",)))

    def test_dropped_export_is_named_from_its_content(self):
        from tests.synthetic import write_wandrer_kmz
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, ".upload.kmz")
            write_wandrer_kmz(p, stamp="20261021-12:00:00", activity="foot")
            self.assertEqual(scan.name_dropped_export(p, geocode=False), "wandrer-2026-10-21-foot.kmz")
            _write(os.path.join(d, ".bad.kmz"), "not a kmz")
            self.assertIsNone(scan.name_dropped_export(os.path.join(d, ".bad.kmz"), geocode=False))


class RegionTests(unittest.TestCase):
    def test_downloaded_and_names(self):
        with tempfile.TemporaryDirectory() as d:
            regions.MAPS_DIR = os.path.join(d, "maps"); regions.GEOFABRIK = os.path.join(d, "geofabrik.json")
            regions._catalog.update(mtime=None, items={})
            os.makedirs(regions.MAPS_DIR)
            _write(os.path.join(regions.MAPS_DIR, "oberbayern-latest.osm.pbf"), b"x" * 10, "wb")
            _write(os.path.join(regions.MAPS_DIR, "other.txt"), "x")
            self.assertEqual(regions.downloaded()[0]["name"], "oberbayern")      # no index yet
            feats = [{"properties": {"id": "europe", "name": "Europe", "urls": {"pbf": "u"}}},
                     {"properties": {"id": "bayern", "name": "Bayern", "parent": "europe", "urls": {"pbf": "u"}}},
                     {"properties": {"id": "oberbayern", "name": "Oberbayern", "parent": "bayern", "urls": {"pbf": "u"}}}]
            _write(regions.GEOFABRIK, json.dumps({"features": feats}))
            regions._catalog.update(mtime=None, items={})
            self.assertEqual(regions.downloaded()[0]["name"], "Oberbayern (Bayern)")
            self.assertEqual(len(regions.downloaded()), 1)

    def test_download_from_file_url(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "src.pbf"); _write(src, b"y" * 100, "wb")
            regions.MAPS_DIR = os.path.join(d, "maps"); regions.GEOFABRIK = os.path.join(d, "g.json")
            _write(regions.GEOFABRIK, json.dumps({"features": [{"properties": {"id": "a/b", "name": "AB", "urls": {"pbf": "file://" + src}}}]}))
            regions._catalog.update(mtime=None, items={})
            dl = regions.Downloader(); dl.start(["a_b"])
            for _ in range(50):
                if not dl.status()["running"]:
                    break
                time.sleep(0.1)
            self.assertEqual(os.path.getsize(os.path.join(regions.MAPS_DIR, "a_b-latest.osm.pbf")), 100)
            self.assertEqual(dl.status()["error"], "")
            with self.assertRaises(ValueError):
                dl.start(["nope"])


class RegionShapeTests(unittest.TestCase):
    """Outlines and tiles of the Regions area mode."""
    def _catalog(self, d, geoms):
        regions.GEOFABRIK = os.path.join(d, "g.json")
        feats = [{"properties": {"id": k, "name": k.title(), "urls": {"pbf": "u"}}, "geometry": g} for k, g in geoms.items()]
        _write(regions.GEOFABRIK, json.dumps({"features": feats}))
        regions._catalog.update(mtime=None, items={})

    def test_square_takes_every_tile_of_its_box(self):
        from wahoondrer.tiles import tiles_for_bbox
        sq = [[11.0, 48.0], [11.9, 48.0], [11.9, 48.6], [11.0, 48.6], [11.0, 48.0]]
        with tempfile.TemporaryDirectory() as d:
            self._catalog(d, {"sq": {"type": "Polygon", "coordinates": [sq, [[11.4, 48.2], [11.5, 48.2], [11.5, 48.3], [11.4, 48.2]]]}})
            want = sorted(f"{x}/{y}" for x, y in tiles_for_bbox(11.0, 48.0, 11.9, 48.6))
            self.assertEqual(regions.tiles_of(["sq"]), want)
            sh = regions.shapes(["sq", "unknown"])
            self.assertEqual(list(sh), ["sq"])
            self.assertEqual(len(sh["sq"]["rings"]), 1)                 # the hole is left out
            self.assertEqual(sh["sq"]["name"], "Sq")

    def test_diagonal_band_skips_the_far_corners(self):
        from wahoondrer.tiles import tiles_for_bbox
        band = [[10.0, 47.0], [10.2, 47.0], [13.0, 49.8], [13.0, 50.0], [12.8, 50.0], [10.0, 47.2], [10.0, 47.0]]
        with tempfile.TemporaryDirectory() as d:
            self._catalog(d, {"band": {"type": "MultiPolygon", "coordinates": [[band]]}})
            got = regions.tiles_of(["band"])
            box = sorted(f"{x}/{y}" for x, y in tiles_for_bbox(10.0, 47.0, 13.0, 50.0))
            self.assertTrue(got and set(got) < set(box))
            xs = sorted({int(t.split("/")[0]) for t in box}); ys = sorted({int(t.split("/")[1]) for t in box})
            self.assertNotIn(f"{xs[-1]}/{ys[-1]}", got)                 # south-east corner: far from the band
            self.assertNotIn(f"{xs[0]}/{ys[0]}", got)                   # north-west corner
            self.assertEqual(regions.tiles_of(["band", "band"]), got)
            self.assertEqual(regions.tiles_of([]), [])


class RegionExtraTests(unittest.TestCase):
    def test_names_and_sorting(self):
        self.assertEqual(regions.clean_name("Østlandet<br />(Eastern Norway)"), "Østlandet (Eastern Norway)")
        names = ["Île de Clipperton", "Østlandet", "Ústecký kraj", "Iowa", "Oberbayern", "Ulm", "Zürich", "Ibiza"]
        got = sorted(names, key=regions.sort_key)
        self.assertEqual(got, ["Ibiza", "Île de Clipperton", "Iowa", "Oberbayern", "Østlandet", "Ulm", "Ústecký kraj", "Zürich"])

    def test_delete(self):
        with tempfile.TemporaryDirectory() as d:
            regions.MAPS_DIR = os.path.join(d, "maps"); os.makedirs(regions.MAPS_DIR)
            _write(os.path.join(regions.MAPS_DIR, "dj-latest.osm.pbf"), b"z" * 7, "wb")
            self.assertEqual(regions.delete("dj"), 7)
            with self.assertRaises(ValueError):
                regions.delete("dj")
            with self.assertRaises(ValueError):
                regions.delete("../x")

    def test_one_failure_does_not_stop_the_queue(self):
        regions.RETRY_DELAYS = (0, 0, 0)
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "src.pbf"); _write(src, b"y" * 50, "wb")
            regions.MAPS_DIR = os.path.join(d, "maps"); regions.GEOFABRIK = os.path.join(d, "g.json")
            feats = [{"properties": {"id": "bad", "name": "Bad", "urls": {"pbf": "file://" + d + "/missing.pbf"}}},
                     {"properties": {"id": "good", "name": "Good", "urls": {"pbf": "file://" + src}}}]
            _write(regions.GEOFABRIK, json.dumps({"features": feats}))
            regions._catalog.update(mtime=None, items={})
            dl = regions.Downloader(); dl.start(["bad", "good"])
            for _ in range(50):
                if not dl.status()["running"]:
                    break
                time.sleep(0.1)
            st = dl.status()
            self.assertIn("bad", st["failed"]); self.assertEqual(st["done"], ["good"])
            self.assertTrue(os.path.isfile(os.path.join(regions.MAPS_DIR, "good-latest.osm.pbf")))
            self.assertFalse(os.path.exists(os.path.join(regions.MAPS_DIR, "bad-latest.osm.pbf.part")))

    def _serve(self, handler_cls):
        import http.server, threading
        srv = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv

    def _wait(self, dl, n=100):
        for _ in range(n):
            if not dl.status()["running"]:
                break
            time.sleep(0.1)

    def test_redirect_loop_is_retried_then_reported(self):
        import http.server

        class H(http.server.BaseHTTPRequestHandler):       # like Geofabrik's 301 to "<url>/"
            def do_GET(self):
                self.send_response(301); self.send_header("Location", self.path.rstrip("/") + "/"); self.end_headers()
            def log_message(self, *a): pass
        srv = self._serve(H); regions.RETRY_DELAYS = (0, 0, 0)
        with tempfile.TemporaryDirectory() as d:
            regions.MAPS_DIR = os.path.join(d, "maps"); regions.GEOFABRIK = os.path.join(d, "g.json")
            url = f"http://127.0.0.1:{srv.server_port}/picardie-latest.osm.pbf"
            _write(regions.GEOFABRIK, json.dumps({"features": [{"properties": {"id": "picardie", "name": "Picardie", "urls": {"pbf": url}}}]}))
            regions._catalog.update(mtime=None, items={})
            dl = regions.Downloader(); dl.start(["picardie"]); self._wait(dl)
            srv.shutdown()
            self.assertIn("Tried 4 times", dl.status()["failed"]["picardie"])

    def test_temporary_failure_then_success_and_failed_goes_last(self):
        import http.server
        hits = {"n": 0}; order = []

        class H(http.server.BaseHTTPRequestHandler):       # 'flaky' fails twice, then works; 'ok' always works
            def do_GET(self):
                order.append(self.path)
                if "flaky" in self.path:
                    if self.path == "/flaky.pbf":
                        hits["n"] += 1
                    if self.path.endswith("/") or hits["n"] <= 2:      # redirect loop the first two times
                        self.send_response(301); self.send_header("Location", self.path.rstrip("/") + "/"); self.end_headers(); return
                self.send_response(200); self.send_header("Content-Length", "20"); self.end_headers(); self.wfile.write(b"z" * 20)
            def log_message(self, *a): pass
        srv = self._serve(H); regions.RETRY_DELAYS = (0, 0, 0)
        with tempfile.TemporaryDirectory() as d:
            regions.MAPS_DIR = os.path.join(d, "maps"); regions.GEOFABRIK = os.path.join(d, "g.json")
            base = f"http://127.0.0.1:{srv.server_port}"
            feats = [{"properties": {"id": k, "name": k, "urls": {"pbf": f"{base}/{k}.pbf"}}} for k in ("flaky", "ok")]
            _write(regions.GEOFABRIK, json.dumps({"features": feats}))
            regions._catalog.update(mtime=None, items={})
            dl = regions.Downloader(); dl.start(["flaky", "ok"]); self._wait(dl)
            srv.shutdown()
            st = dl.status()
            self.assertEqual(st["failed"], {}); self.assertEqual(sorted(st["done"]), ["flaky", "ok"])
            self.assertEqual(st["done"][0], "ok")                       # the failing one was pushed back
            self.assertEqual(order[0], "/flaky.pbf"); self.assertIn("/ok.pbf", order)

    def test_404_is_final(self):
        import http.server

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(404); self.end_headers()
            def log_message(self, *a): pass
        srv = self._serve(H)
        with tempfile.TemporaryDirectory() as d:
            regions.MAPS_DIR = os.path.join(d, "maps"); regions.GEOFABRIK = os.path.join(d, "g.json")
            _write(regions.GEOFABRIK, json.dumps({"features": [{"properties": {"id": "x", "name": "X", "urls": {"pbf": f"http://127.0.0.1:{srv.server_port}/x.pbf"}}}]}))
            regions._catalog.update(mtime=None, items={})
            dl = regions.Downloader(); dl.start(["x"]); self._wait(dl)
            srv.shutdown()
            self.assertIn("any more", dl.status()["failed"]["x"])

    def test_link_data_dir(self):
        with tempfile.TemporaryDirectory() as d:
            regions.WMC_DIR = os.path.join(d, "home", "wmc"); os.makedirs(os.path.dirname(regions.WMC_DIR))
            base = os.path.join(d, "wd")
            os.makedirs(os.path.join(regions.WMC_DIR, "_download"))
            _write(os.path.join(regions.WMC_DIR, "_download", "f"), "x")
            self.assertEqual(regions.link_data_dir(base), "needs_move")          # never moves by itself
            self.assertEqual(regions.link_data_dir(base, move=True), "moved")
            self.assertTrue(os.path.islink(regions.WMC_DIR))
            self.assertTrue(os.path.isfile(os.path.join(base, "OSM_data", "_download", "f")))
            self.assertTrue(regions.data_dir_info(base)["ok"])
            self.assertEqual(regions.link_data_dir(base), "already")


class CommandTests(unittest.TestCase):
    def s(self, **over):
        s = settings.load(os.devnull)
        s["tiles"] = ["136/88", "135/88"]
        s["folders"]["out"] = os.path.join(tempfile.gettempdir(), "wd-out-test")
        s["folders"]["work"] = os.path.join(tempfile.gettempdir(), "wd-work-test")
        for k, v in over.items():
            s[k].update(v)
        return s

    def test_parse_tiles(self):
        self.assertEqual(jobs.parse_tiles(["136/88", "135/88", "136/88"]), ["135/88", "136/88"])
        for bad in ("136", "a/b", "300/1", "1/2/3"):
            with self.assertRaises(jobs.SettingsError):
                jobs.parse_tiles([bad])

    def test_full_chain(self):
        cmds = jobs.build_commands(self.s(squadrats={"enabled": True}), ["/x/a.kmz"], "/x/sq.kml")
        self.assertEqual(len(cmds), 2)
        a, b = cmds[0][1], cmds[1][1]
        self.assertIn("--squadrats", a); self.assertIn("/x/sq.kml", a)
        self.assertEqual(a[a.index("--squadrats-buffer") + 1], "3")
        self.assertNotIn("--no-boundaries", a)
        self.assertEqual(b[b.index("--xy") + 1], "135/88,136/88")
        self.assertIn("--wandrer", b)

    def test_squadrats_scopes_and_old_settings(self):
        b = jobs.build_commands(self.s(squadrats={"enabled": True}), ["/x/a.kmz"], "/x/sq.kml")[0][1]
        self.assertEqual((b[b.index("--squadrats-big-scope") + 1], b[b.index("--squadrats-small-scope") + 1]), ("all", "all"))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            _write(p, json.dumps({"squadrats": {"small_scope": "explored-big", "buffer": 3}}))   # saved before "all"
            self.assertEqual(settings.load(p)["squadrats"]["small_scope"], "all")
            _write(p, json.dumps({"squadrats": {"small_scope": "explored-big", "big_scope": "band"}}))
            self.assertEqual(settings.load(p)["squadrats"]["small_scope"], "explored-big")   # a choice made since

    def test_osm_max_days_is_passed(self):
        b = jobs.build_commands(self.s(), ["/x/a.kmz"], None)[-1][1]
        self.assertEqual(b[b.index("--max-days") + 1], "14")
        s = self.s(); s["osm_max_days"] = 0
        b = jobs.build_commands(s, ["/x/a.kmz"], None, plan=True)[-1][1]
        self.assertEqual(b[b.index("--max-days") + 1], "0")          # never renew

    def test_osm_max_days_values(self):
        self.assertEqual(settings.osm_max_days({}), 14)
        for v in settings.OSM_MAX_DAYS:
            self.assertEqual(settings.osm_max_days({"osm_max_days": v}), v)
        for bad in (5, "30", None, True, -1):
            self.assertEqual(settings.osm_max_days({"osm_max_days": bad}), 14)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            s = settings.load(p); s["osm_max_days"] = 60; s = settings.save(s, p)
            s["device"] = "garmin"; g = settings.save(s, p)                # shared by the devices
            self.assertEqual(g["osm_max_days"], 60)

    def test_squadrats_without_file_is_left_out(self):
        for plan in (False, True):
            cmds = jobs.build_commands(self.s(squadrats={"enabled": True}), [], None, plan=plan)
            self.assertIn("! Squadrats: no KML file", cmds[0][1][-1])        # a note, not an error
            self.assertNotIn("--squadrats", cmds[-1][1])
            self.assertEqual(cmds[-1][0], "Plan: tiles and OSM regions" if plan else "Build the map tiles")

    def test_squadrats_without_file_keeps_wandrer(self):
        cmds = jobs.build_commands(self.s(squadrats={"enabled": True}), ["/x/a.kmz"], None)
        convert = [c for c in cmds if c[0].startswith("Convert")][0][1]
        self.assertNotIn("--squadrats", convert); self.assertIn("/x/a.kmz", convert)
        self.assertIn("--wandrer", cmds[-1][1])

    def test_options_are_passed(self):
        cmds = jobs.build_commands(self.s(wandrer={"boundaries": False, "radius_m": 0,
                                                   "with_traveled": True, "paved_always": True}), ["/x/a.kmz"], None)
        a = cmds[0][1]
        self.assertIn("--paved-always", a); self.assertNotIn("--no-merge", a)
        self.assertIn("--no-boundaries", a); self.assertIn("--with-traveled", a)
        self.assertEqual(a[a.index("--radius-m") + 1], "0")

    def test_default_look_adds_no_option(self):
        b = jobs.build_commands(self.s(), ["/x/a.kmz"], None)[-1][1]
        self.assertNotIn("--theme-look", b)

    def test_custom_look_is_passed_to_the_build_only(self):
        look = {"light": {"unvisited": {"color": "#00aaff", "width": 2.0, "outline": False}}}
        s = self.s(wandrer={"look": look})
        b = jobs.build_commands(s, ["/x/a.kmz"], None)[-1][1]
        sent = json.loads(b[b.index("--theme-look") + 1])
        self.assertEqual(sent["light"]["unvisited"], {"color": "#00AAFF", "width": 2.0, "outline": False})
        self.assertEqual(sent["dark"]["visited"]["color"], "#4CCB7A")          # what was not changed keeps its default
        self.assertNotIn("--theme-look", jobs.build_commands(s, ["/x/a.kmz"], None, plan=True)[-1][1])

    def test_bad_look_is_refused(self):
        with self.assertRaises(jobs.SettingsError):
            jobs.build_commands(self.s(wandrer={"look": {"dark": {"visited": {"color": "green"}}}}), ["/x/a.kmz"], None)

    def test_contour_flags(self):
        s = self.s(); s["build"] = {"skip_classic": False, "contour": True}
        b = jobs.build_commands(s, [], None)[-1][1]
        self.assertIn("--contour", b); self.assertNotIn("--srtm1", b)
        self.assertEqual(b[b.index("--contour-spacing") + 1], "25-75-150")
        s["build"]["contour_spacing"] = "30-90-180"
        b = jobs.build_commands(s, [], None)[-1][1]
        self.assertEqual(b[b.index("--contour-spacing") + 1], "30-90-180")
        s["build"].update({"contour_spacing": "25-75-150", "contour_per_tile": True,
                           "contour_tiles": {"136/88": "none", "136/89": "30-90-180", "999/999": "none", "135/88": "25-75-150"}})
        s["tiles"] = ["136/88", "136/89", "135/88"]
        b = jobs.build_commands(s, [], None)[-1][1]
        pairs = [b[i + 1] for i, a in enumerate(b) if a == "--contour-tile"]
        self.assertEqual(sorted(pairs), ["136/88=none", "136/89=30-90-180"])   # default and unselected tiles are left out
        s["build"]["contour_per_tile"] = False
        self.assertNotIn("--contour-tile", jobs.build_commands(s, [], None)[-1][1])
        s["build"]["contour_spacing"] = "20-50-100"
        with self.assertRaises(jobs.SettingsError):
            jobs.build_commands(s, [], None)
        s["build"]["contour_spacing"] = "25-75-150"
        s["build"]["contour"] = False
        self.assertNotIn("--contour", jobs.build_commands(s, [], None)[-1][1])

    def test_plan_only_builds_tiles_plan(self):
        cmds = jobs.build_commands(self.s(), ["/x/a.kmz"], None, plan=True)
        self.assertEqual(len(cmds), 1); self.assertIn("--plan", cmds[0][1])
        self.assertNotIn("--wandrer", cmds[0][1])

    def test_classic_only_without_wandrer(self):
        cmds = jobs.build_commands(self.s(wandrer={"enabled": False}), ["/x/a.kmz"], None)
        self.assertEqual(len(cmds), 1); self.assertNotIn("--wandrer", cmds[0][1])

    def test_conversion_skipped_when_no_export_covers_the_tiles(self):
        from unittest import mock
        s = self.s(); tile = s["tiles"][0]; self.assertEqual(tile, "136/88")
        far = {"tiles": [[0, 0]]}
        near = {"tiles": [[int(v) for v in tile.split("/")]]}
        with mock.patch("wahoondrer.gui.scan.cached_info", return_value=far):
            cmds = jobs.build_commands(s, ["/x/a.kmz"], None)
        self.assertEqual(len(cmds), 2)
        self.assertTrue(all("kmz_to_osm" not in " ".join(c[1]) for c in cmds))
        self.assertNotIn("--wandrer", cmds[1][1])
        with mock.patch("wahoondrer.gui.scan.cached_info", return_value=near):
            cmds = jobs.build_commands(s, ["/x/a.kmz"], None)
        self.assertIn("--wandrer", cmds[-1][1])

    def test_errors(self):
        cmds = jobs.build_commands(self.s(squadrats={"enabled": True}), [], "/x/sq.kml")   # squadrats alone
        self.assertEqual(len(cmds), 2)
        self.assertIn("--squadrats", cmds[0][1]); self.assertIn("--tiles", cmds[0][1])
        self.assertIn("--wandrer", cmds[1][1])
        s = self.s(); s["folders"]["out"] = os.path.join(jobs.REPO, "out")
        with self.assertRaises(jobs.SettingsError):      # output inside the repo
            jobs.build_commands(s, [], None)
        s = self.s(); s["tiles"] = []
        with self.assertRaises(jobs.SettingsError):
            jobs.build_commands(s, [], None)


class SettingsTests(unittest.TestCase):
    def test_standard_folders(self):
        with tempfile.TemporaryDirectory() as d:
            paths = settings.make_standard_folders(d)
            self.assertEqual(set(paths), {"wandrer", "squadrats", "out", "work"})
            self.assertEqual(sorted(os.path.basename(p) for p in paths.values()),
                             ["Output", "Squadrats_exports", "Wandrer_exports", "Work_files"])
            self.assertTrue(all(os.path.isdir(p) for p in paths.values()))

    def test_roundtrip_ignores_unknown_keys(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            s = settings.load(p)
            s["squadrats"]["buffer"] = 7; s["bogus"] = 1
            saved = settings.save(s, p)
            self.assertNotIn("bogus", saved)
            self.assertEqual(settings.load(p)["squadrats"]["buffer"], 7)

    def test_fold_choices_survive_a_save(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            s = settings.load(p)
            self.assertEqual(s["ui"]["folded"], {})
            s["ui"]["folded"] = {"sec:osm": False, "reg:Germany > Bayern": True, "tile:136": True}
            settings.save(s, p)
            s2 = settings.load(p)
            self.assertEqual(s2["ui"]["folded"], {"sec:osm": False, "reg:Germany > Bayern": True, "tile:136": True})
            self.assertEqual(s2["ui"]["sections"], {})

    def test_settings_written_before_folding_existed_still_load(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            _write(p, json.dumps({"ui": {"sections": {"pois": "pro"}}}))
            s = settings.load(p)
            self.assertEqual(s["ui"]["sections"], {"pois": "pro"})
            self.assertEqual(s["ui"]["folded"], {})


class NotifySettingTests(unittest.TestCase):
    def test_notification_is_on_by_default_and_the_choice_is_kept(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            _write(p, json.dumps({"build": {"skip_classic": True}}))        # written before the option existed
            s = settings.load(p)
            self.assertIs(s["build"]["notify"], True)
            self.assertIs(s["build"]["skip_classic"], True)
            s["build"]["notify"] = False
            settings.save(s, p)
            self.assertIs(settings.load(p)["build"]["notify"], False)


class NativeNotifyTests(unittest.TestCase):
    """native.notify builds the right command on each system, and nothing typed in the text can change it."""

    def call(self, platform, *args, which="/usr/bin/notify-send", **kw):
        with mock.patch.object(native.sys, "platform", platform), \
                mock.patch.object(native.shutil, "which", return_value=which), \
                mock.patch.object(native.subprocess, "Popen") as popen:
            result = native.notify(*args, **kw)
        return result, popen

    def test_macos_passes_title_text_and_sound_as_separate_arguments(self):
        nasty = 'Finished "in" 5 s; do shell script "rm -rf ~" \\ $(x) `y`'
        ok, popen = self.call("darwin", "maprBoy: maps ready", nasty)
        self.assertTrue(ok)
        argv = popen.call_args[0][0]
        self.assertEqual(argv[:2], ["osascript", "-e"])
        self.assertNotIn("rm -rf", argv[2])                         # the script never contains the text
        self.assertEqual(argv[3:], ["maprBoy: maps ready", nasty, "Glass"])
        _, popen = self.call("darwin", "t", "x", ok=False)
        self.assertEqual(popen.call_args[0][0][-1], "Basso")

    def test_linux_uses_notify_send_with_urgency(self):
        ok, popen = self.call("linux", "maprBoy: stopped", "-oops", ok=False)
        self.assertTrue(ok)
        argv = popen.call_args[0][0]
        self.assertEqual(argv[0], "notify-send")
        self.assertEqual(argv[argv.index("-u") + 1], "critical")
        self.assertEqual(argv[-3:], ["--", "maprBoy: stopped", "-oops"])       # "--": a text starting with "-" is not an option
        _, popen = self.call("linux", "t", "x")
        self.assertEqual(popen.call_args[0][0][popen.call_args[0][0].index("-u") + 1], "normal")

    def test_linux_without_notify_send_does_nothing(self):
        ok, popen = self.call("linux", "t", "x", which=None)
        self.assertFalse(ok)
        popen.assert_not_called()

    def test_windows_passes_the_text_in_the_environment_not_in_the_script(self):
        ok, popen = self.call("win32", "maprBoy: maps ready", "it's \"done\"")
        self.assertTrue(ok)
        argv, kw = popen.call_args[0][0], popen.call_args[1]
        self.assertEqual(argv[0], "powershell")
        self.assertNotIn("done", argv[-1])
        self.assertEqual((kw["env"]["MAPRBOY_TITLE"], kw["env"]["MAPRBOY_TEXT"]), ("maprBoy: maps ready", "it's \"done\""))
        self.assertIn("Error", self.call("win32", "t", "x", ok=False)[1].call_args[0][0][-1])

    def test_never_raises(self):
        with mock.patch.object(native.sys, "platform", "darwin"), \
                mock.patch.object(native.subprocess, "Popen", side_effect=FileNotFoundError("osascript")):
            self.assertFalse(native.notify("t", "x"))

    def test_text_is_one_short_printable_line(self):
        _, popen = self.call("darwin", "a\nb\x07\tc", "line one\nline two " + "x" * 500)
        argv = popen.call_args[0][0]
        self.assertEqual(argv[3], "a b c")
        self.assertNotIn("\n", argv[4])
        self.assertLessEqual(len(argv[4]), 220)
        self.assertTrue(argv[4].endswith("\u2026"))


def run_job(commands, kind="run", notify=True, wait=30):
    """Run commands in a Job with native.notify replaced by a mock; return (job, mock)."""
    job = jobs.Job()
    with mock.patch.object(jobs.native, "notify") as note:
        job.start(commands, kind, notify=notify)
        job.thread.join(wait)
    return job, note


def py(code):
    return [sys.executable, "-c", code]


class EstimateTests(unittest.TestCase):
    """The time estimate shown before a run: only from comparable, consistent past runs."""
    P = {"device": "wahoo", "tiles": 4, "skip_classic": False, "contour": False}

    def test_record_and_estimate(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "runs.json")
            jobs.record_run(self.P, 400, path)
            self.assertIsNone(jobs.per_tile_estimate(jobs.load_runs(path), "wahoo", False, False))   # one run: too few
            jobs.record_run(dict(self.P, tiles=2), 240, path)
            est = jobs.per_tile_estimate(jobs.load_runs(path), "wahoo", False, False)
            self.assertEqual(est, {"seconds_per_tile": 110.0, "runs": 2})
            self.assertIsNone(jobs.per_tile_estimate(jobs.load_runs(path), "wahoo", True, False))    # other kind of run
            self.assertIsNone(jobs.per_tile_estimate(jobs.load_runs(path), "garmin", False, False))

    def test_too_different_runs_give_nothing(self):
        runs = [dict(self.P, seconds=100), dict(self.P, seconds=1000)]
        self.assertIsNone(jobs.per_tile_estimate(runs, "wahoo", False, False))

    def test_history_is_capped_and_ignores_empty_runs(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "runs.json")
            jobs.record_run(dict(self.P, tiles=0), 50, path)
            self.assertEqual(jobs.load_runs(path), [])
            for i in range(jobs.RUNS_KEPT + 5):
                jobs.record_run(self.P, 100 + i, path)
            runs = jobs.load_runs(path)
            self.assertEqual(len(runs), jobs.RUNS_KEPT)
            self.assertEqual(runs[-1]["seconds"], 100 + jobs.RUNS_KEPT + 4)
            _write(path, "not json")
            self.assertEqual(jobs.load_runs(path), [])

    def test_profile(self):
        with tempfile.TemporaryDirectory() as d:
            s = settings.load(os.path.join(d, "s.json"))
        s["tiles"] = ["1/2", "3/4"]; s["build"]["contour"] = True
        self.assertEqual(jobs.run_profile(s), {"device": "wahoo", "tiles": 2, "skip_classic": False, "contour": True})


class JobNotificationTests(unittest.TestCase):
    def test_done_run_notifies_with_the_duration(self):
        job, note = run_job([("Build the map tiles", py("print('hello')"))])
        self.assertEqual(job.status, "done")
        note.assert_called_once()
        title, text = note.call_args[0]
        self.assertIn("ready", title)
        self.assertIn("Finished in", text)
        self.assertNotIn("note", text)
        self.assertNotIn("ok", note.call_args[1])                      # default: a good news notification

    def test_notes_to_read_are_counted(self):
        job, note = run_job([("Build", py("print('! Tile 1/1 has no Wandrer data'); print('! another')"))])
        self.assertIn("2 notes to read", note.call_args[0][1])
        job, note = run_job([("Build", py("print('! one')"))])
        self.assertIn("1 note to read", note.call_args[0][1])

    def test_failed_run_notifies_with_the_step_name(self):
        job, note = run_job([("Convert the exports", py("print('fine')")),
                             ("Build the map tiles", py("import sys; print('boom'); sys.exit(3)"))])
        self.assertEqual(job.status, "failed")
        note.assert_called_once()
        title, text = note.call_args[0]
        self.assertIn("stopped", title)
        self.assertIn("Build the map tiles", text)
        self.assertNotIn("2/2", text)
        self.assertIs(note.call_args[1]["ok"], False)

    def test_missing_module_is_explained_in_the_log_and_the_notification(self):
        job, note = run_job([("Build the map tiles", py("import a_module_that_is_not_installed_xyz"))])
        self.assertEqual(job.status, "failed")
        log = "\n".join(job.lines)
        self.assertIn("Stopped: this step failed", log)
        self.assertIn("Hint:", log)
        self.assertIn("a_module_that_is_not_installed_xyz", log)
        self.assertIn("wahoondrer-env", note.call_args[0][1])
        self.assertLess(log.index("Stopped: this step failed"), log.index("Hint:"))
        self.assertLess(log.index("Hint:"), log.index("Time spent"))

    def test_no_notification_when_cancelled_for_a_plan_or_when_switched_off(self):
        job = jobs.Job()
        with mock.patch.object(jobs.native, "notify") as note:
            job.start([("Slow", py("import time; time.sleep(60)"))], "run", notify=True)
            for _ in range(200):
                if job.proc:
                    break
                time.sleep(0.05)
            job.cancel()
            job.thread.join(30)
        self.assertEqual(job.status, "cancelled")
        note.assert_not_called()
        for kind, notify in (("plan", True), ("run", False)):
            job, note = run_job([("Quick", py("print('x')"))], kind=kind, notify=notify)
            self.assertEqual(job.status, "done")
            note.assert_not_called()

    def test_a_plan_that_fails_still_shows_the_hint_but_does_not_notify(self):
        job, note = run_job([("Plan", py("import a_module_that_is_not_installed_xyz"))], kind="plan")
        self.assertEqual(job.status, "failed")
        self.assertIn("Hint:", "\n".join(job.lines))
        note.assert_not_called()

    def test_default_is_silent(self):
        job = jobs.Job()
        with mock.patch.object(jobs.native, "notify") as note:
            job.start([("Quick", py("print('x')"))])
            job.thread.join(30)
        note.assert_not_called()


class HintTests(unittest.TestCase):
    def test_old_wmc_import_error_is_explained(self):
        lines = ["ImportError: cannot import name 'download_tooling' from 'wahoomc.downloader' (/x/wahoomc/downloader.py)"]
        short, long = jobs.hint_for(lines)
        self.assertIn("too old", short); self.assertIn("4.4.0", short)
        self.assertIn("pip install wahoomc==4.4.0", long)

    def test_missing_module(self):
        short, long = jobs.hint_for(["Traceback (most recent call last):", "ModuleNotFoundError: No module named 'wahoomc'"])
        self.assertIn("'wahoomc'", short)
        self.assertIn("wahoondrer-env", short)
        self.assertTrue(long.startswith("Hint:"))
        self.assertIn("'wahoomc'", long)

    def test_submodule_name_is_kept(self):
        self.assertIn("osgeo._gdal", jobs.hint_for(["ModuleNotFoundError: No module named 'osgeo._gdal'"])[0])

    def test_disk_full(self):
        for line in ("OSError: [Errno 28] No space left on device", "OSError: [Errno 112] There is not enough space on the disk"):
            self.assertIn("disk is full", jobs.hint_for(["x", line])[0])

    def test_tile_in_the_open_sea(self):
        wmc = "Given XY coordinate '120/90' consists of no countries. Please check if your input is valid."
        short, long = jobs.hint_for([wmc])
        self.assertIn("120/90", short)
        self.assertIn("no land", short)
        self.assertTrue(long.startswith("Hint:"))
        self.assertIn("open sea", long)

    def test_unknown_failure_has_no_hint(self):
        self.assertIsNone(jobs.hint_for(["Traceback", "ValueError: nope"]))
        self.assertIsNone(jobs.hint_for([]))

    def test_only_the_end_of_the_log_counts(self):
        old = ["ModuleNotFoundError: No module named 'wahoomc'"] + ["line"] * 100
        self.assertIsNone(jobs.hint_for(old))


class EnvironmentTests(unittest.TestCase):
    def check(self, absent, wmc=None):
        real = jobs.importlib.util.find_spec
        fake = lambda name, *a: None if name in absent else real("json")
        with mock.patch.object(jobs.importlib.util, "find_spec", side_effect=fake), \
                mock.patch.object(jobs, "installed_version", return_value=wmc):
            return jobs.check_environment()

    def test_old_wmc_stops_the_build_and_says_how_to_update(self):
        env = self.check(set(), wmc="4.2.0")
        self.assertEqual(env["missing"], [])
        for word in ("4.2.0", "4.4.0", "pip install wahoomc==4.4.0", "wahoondrer-env"):
            self.assertIn(word, env["message"])
        self.assertEqual(env["notice"], "")

    def test_same_wmc_is_fine_and_a_newer_one_only_gets_a_note(self):
        for ok in ("4.4.0", "4.4.0.post1"):
            env = self.check(set(), wmc=ok)
            self.assertEqual((env["message"], env["notice"]), ("", ""), ok)
        env = self.check(set(), wmc="4.10.0")                    # 4.10 is newer than 4.4 (numbers, not text)
        self.assertEqual(env["message"], "")
        self.assertIn("4.10.0", env["notice"]); self.assertIn("not guaranteed", env["notice"])

    def test_an_unreadable_wmc_version_is_not_a_reason_to_stop(self):
        env = self.check(set(), wmc=None)
        self.assertEqual((env["message"], env["notice"], env["wmc_version"]), ("", "", None))

    def test_python_too_old_stops_the_build(self):
        with mock.patch.object(jobs, "MIN_PYTHON", (99, 0)):
            env = self.check(set())
        self.assertIn("99.0 or newer", env["message"]); self.assertIn("wahoondrer-env", env["message"])

    def test_missing_module_and_old_wmc_are_both_reported(self):
        env = self.check({"osgeo"}, wmc="4.2.0")
        self.assertIn("osgeo", env["message"]); self.assertIn("4.2.0", env["message"])

    def test_version_numbers_compare_as_numbers(self):
        self.assertLess(jobs.version_tuple("4.4.0"), jobs.version_tuple("4.10.0"))
        self.assertEqual(jobs.version_tuple("4.5.0rc1"), (4, 5, 0))
        self.assertEqual(jobs.version_tuple("garbage"), ())

    def test_the_required_version_is_the_pinned_one(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name in ("requirements.txt", "environment.yml"):
            with open(os.path.join(root, name), encoding="utf-8") as f:
                self.assertRegex(f.read(), r"(?m)^\s*(?:- )?wahoomc==" + re.escape(jobs.WMC_VERSION) + r"\s*$", name)

    def test_everything_installed(self):
        env = self.check(set())
        self.assertEqual(env["missing"], [])
        self.assertEqual(env["message"], "")
        self.assertEqual(env["python"], sys.executable)
        self.assertTrue(env["version"][0].isdigit())

    def test_missing_modules_are_named_with_the_way_out(self):
        env = self.check({"wahoomc", "osgeo"})
        self.assertEqual(env["missing"], ["wahoomc", "osgeo"])
        for word in ("wahoomc", "osgeo", sys.executable, "wahoondrer-env", "Ctrl+C"):
            self.assertIn(word, env["message"])
        self.assertNotIn("shapely", env["message"])

    def test_a_module_that_cannot_even_be_looked_up_counts_as_missing(self):
        with mock.patch.object(jobs.importlib.util, "find_spec", side_effect=ValueError("bad")):
            self.assertEqual(jobs.check_environment()["missing"], list(jobs.REQUIRED_MODULES))

    def test_require_environment_refuses_to_start_a_build(self):
        with mock.patch.object(jobs, "check_environment", return_value={"message": "Python module not found: wahoomc."}):
            with self.assertRaises(jobs.SettingsError) as cm:
                jobs.require_environment()
        self.assertIn("wahoomc", str(cm.exception))
        with mock.patch.object(jobs, "check_environment", return_value={"message": ""}):
            jobs.require_environment()

    def test_the_page_receives_the_result(self):
        import copy
        with mock.patch.object(jobs, "check_environment", return_value={"missing": ["wahoomc"], "message": "m"}):
            self.assertEqual(server.state(copy.deepcopy(settings.DEFAULTS))["env"]["missing"], ["wahoomc"])


class ContourTests(unittest.TestCase):
    def test_command_matches_wmc_settings_and_needs_no_account(self):
        from wahoondrer import contour
        tile = {"left": 11.25, "bottom": 47.98, "right": 12.65, "top": 48.92}
        c = contour.build_command("/x/pyhgtmap", tile, "/t/elevation", "/h", 3, "10-50-100")
        self.assertEqual(c[0], "/x/pyhgtmap")
        self.assertIn("--area=11.25:47.98:12.65:48.92", c)
        self.assertEqual(c[c.index("-o") + 1], "/t/elevation")
        for opt in ("--sources=view1,view3", "--jobs=3", "--max-nodes-per-tile=0",
                    "--no-zero-contour", "--hgtdir=/h", "--start-node-id=20000000000"):
            self.assertIn(opt, c)
        self.assertEqual(c[c.index("-c") + 1], "100,50")
        self.assertEqual(c[c.index("-s") + 1], "10")
        self.assertFalse([a for a in c if "earthexplorer" in a or "srtm" in a])

    def test_area_west_of_greenwich_is_not_read_as_an_option(self):
        """Brittany, Spain, the UK... start with a minus sign: "-a -5.6:47.5:..." made pyhgtmap stop
        with "expected one argument", so the area is a single "--area=..." argument."""
        from wahoondrer import contour
        tile = {"left": -5.625, "bottom": 47.5, "right": -2.8125, "top": 48.9}
        c = contour.build_command("/x/pyhgtmap", tile, "/t/elevation", "/h", 1, "25-75-150")
        self.assertIn("--area=-5.625:47.5:-2.8125:48.9", c)
        self.assertNotIn("-a", c)
        for flag in ("-o", "-s", "-c"):             # a value that starts with "-" would be taken for an option
            self.assertFalse(c[c.index(flag) + 1].startswith("-"))
        south = {"left": 11.25, "bottom": -33.9, "right": 12.65, "top": -32.5}
        self.assertIn("--area=11.25:-33.9:12.65:-32.5", contour.build_command("/x/pyhgtmap", south, "/t/e", "/h"))

    def test_spacing_presets(self):
        from wahoondrer import contour
        tile = {"left": 11, "bottom": 47, "right": 12, "top": 48}
        for name, (step, major, medium) in contour.PRESETS.items():
            self.assertEqual(major % step, 0, name)       # every line falls on an existing step
            self.assertEqual(medium % step, 0, name)
            c = contour.build_command("/x", tile, "/t/e", "/h", 1, name)
            self.assertEqual(c[c.index("-s") + 1], str(step))
            self.assertEqual(c[c.index("-c") + 1], f"{major},{medium}")
        self.assertEqual(contour.build_command("/x", tile, "/t/e", "/h")[
            contour.build_command("/x", tile, "/t/e", "/h").index("-s") + 1], "25")
        with self.assertRaises(ValueError):
            contour.preset("20-50-100")

    def test_park_and_unpark_hide_the_file_from_wmc(self):
        import tempfile
        from wahoondrer import contour
        d = tempfile.mkdtemp()
        f = os.path.join(d, "elevation_lon1_2lat3_4_view1.osm")
        open(f, "w").write("x")
        contour.park(d)
        self.assertFalse(glob.glob(os.path.join(d, "elevation*.osm")))     # WMC would not merge it
        self.assertTrue(os.path.isfile(f + ".off"))
        contour.unpark(d)
        self.assertTrue(os.path.isfile(f))
        self.assertEqual(contour.choice("none"), "none")
        with self.assertRaises(ValueError):
            contour.choice("fine")

    def test_spacing_marker(self):
        import tempfile
        from wahoondrer import contour
        d = tempfile.mkdtemp()
        self.assertEqual(contour.read_marker(d), contour.LEGACY_PRESET)   # files made before the setting
        contour.write_marker(d, "30-90-180")
        self.assertEqual(contour.read_marker(d), "30-90-180")

    def test_file_ok_rejects_empty_or_unfinished_files(self):
        import tempfile
        from wahoondrer import contour
        d = tempfile.mkdtemp()
        def mk(name, data):
            p = os.path.join(d, name)
            open(p, "wb").write(data)
            return p
        head = b'<?xml version="1.0"?>\n<osm version="0.6">\n' + b"<node id='1'/>\n" * 100
        self.assertTrue(contour.file_ok(mk("ok.osm", head + b"</osm>\n")))
        self.assertFalse(contour.file_ok(mk("cut.osm", head)))                    # killed run
        self.assertFalse(contour.file_ok(mk("empty.osm", b"<osm>\n</osm>\n")))  # header only
        self.assertFalse(contour.file_ok(os.path.join(d, "missing.osm")))

    def test_pyhgtmap_path_prefers_own_environment(self):
        import tempfile
        from wahoondrer import contour
        d = tempfile.mkdtemp()
        old = contour.VENV
        try:
            contour.VENV = d
            b = os.path.join(d, "Scripts" if os.name == "nt" else "bin")
            os.makedirs(b)
            exe = os.path.join(b, "pyhgtmap.exe" if os.name == "nt" else "pyhgtmap")
            open(exe, "w").close()
            self.assertEqual(contour.pyhgtmap_path(), exe)
        finally:
            contour.VENV = old


class PoiTests(unittest.TestCase):
    def setUp(self):
        from wahoondrer import poi_config
        self.pc = poi_config
        self.base = poi_config.read_rules()
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults_without_choices(self):
        eff = self.pc.effective_pois(self.base, {})
        self.assertEqual(len(eff), len(self.base))
        self.assertFalse(self.pc.has_user_choices({"overrides": {}, "custom": []}))

    def test_overrides_and_custom(self):
        user = {"overrides": {"amenity=fuel": {"enabled": False}, "amenity=cafe": {"zoom": 12, "icon": "shape:square:red"}},
                "custom": [{"key": "amenity", "value": "bench", "zoom": 14, "icon": "shape:circle:blue"}]}
        eff = {self.pc.poi_id(p): p for p in self.pc.effective_pois(self.base, user)}
        self.assertFalse(eff["amenity=fuel"]["enabled"])
        self.assertEqual(eff["amenity=cafe"]["zoom"], 12)
        self.assertTrue(eff["amenity=bench"]["custom"])

    def test_variant_icons_are_chosen_one_by_one(self):
        user = {"overrides": {"amenity=toilets": {"variants": {"fee=yes": "shape:square:purple", "fee=maybe": "x.svg"}}}}
        t = {self.pc.poi_id(p): p for p in self.pc.effective_pois(self.base, user)}["amenity=toilets"]
        icons = {v["value"]: v["icon"] for v in t["variants"]}
        self.assertEqual(icons, {"yes": "shape:square:purple", "no": "toilets_green.svg"})    # the others keep their icon
        self.assertEqual(t["icon"], "toilets_blue.svg")
        self.pc.validate([t], self.pc.Paths().icon_dir)
        t2 = {self.pc.poi_id(p): p for p in self.pc.effective_pois(self.base, {"overrides": {"amenity=toilets": {"icon": "shape:circle:red"}}})}["amenity=toilets"]
        self.assertEqual(t2["icon"], "shape:circle:red")                                      # the default icon only
        self.assertEqual(len(t2["variants"]), 2)

    def test_hidden_variants_are_not_drawn(self):
        def toilets(off):
            user = {"overrides": {"amenity=toilets": {"off": off}}}
            return self.pc.theme_block([p for p in self.pc.effective_pois(self.base, user) if self.pc.poi_id(p) == "amenity=toilets"])
        shown = "\n".join(toilets([]))
        self.assertIn('v="yes"', shown); self.assertIn('v="-|yes|no"', shown)
        paid = "\n".join(toilets(["fee=yes"]))
        self.assertNotIn('<m k="fee" v="yes">', paid)
        self.assertIn('v="-|yes|no"', paid)                  # paid toilets do not fall back on the default icon
        self.assertIn("toilets_green.svg", paid); self.assertIn("toilets_blue.svg", paid)
        nodefault = "\n".join(toilets(["other"]))
        self.assertNotIn("toilets_blue.svg", nodefault); self.assertIn("toilets_red.svg", nodefault)
        none = "\n".join(toilets(["fee=yes", "fee=no", "other"]))
        self.assertNotIn("<symbol", none)

    WATER = ("amenity=fountain", "man_made=water_tap", "natural=spring", "amenity=water_point")

    def test_water_groups(self):
        eff = {self.pc.poi_id(p): p for p in self.pc.effective_pois(self.base, {})}
        drink = [p for p in eff.values() if p.get("group") == "drinking_water"]
        other = [p for p in eff.values() if p.get("group") == "other_water"]
        self.assertEqual(len(drink), 5); self.assertEqual(len(other), 4)
        self.assertTrue(all(p["group_label"] == "Drinking water" and p["enabled"] and p["zoom"] == 14 and p["icon"] == "water_blue.svg" for p in drink))
        self.assertTrue(all(p["group_label"] == "Other water sources" and not p["enabled"] and p["zoom"] == 15 and p["icon"] == "water_brown.svg" for p in other))
        self.assertNotIn("only_if", eff["amenity=drinking_water"])
        for pid in self.WATER:
            drinkable, rest = eff[pid + "+drinkable"], eff[pid]
            self.assertEqual(drinkable["only_if"], {"drinking_water": ["yes"]})           # drinkable: tagged drinking_water=yes
            self.assertEqual(rest["hide_if"]["drinking_water"], ["yes"])                  # the others: everything else
            for p in (drinkable, rest):
                self.assertIn("private", p["hide_if"]["access"])
        from wahoondrer.gui import server
        cat = {p["id"]: p for p in server.poi_catalog()["pois"]}
        self.assertEqual(cat["amenity=fountain"]["group"], "other_water")
        self.assertEqual(cat["amenity=fountain+drinkable"]["group"], "drinking_water")
        self.assertEqual(cat["amenity=toilets"]["group"], "")

    def test_drinkable_and_other_fountains_are_drawn_apart(self):
        on = [p for p in self.pc.effective_pois(self.base, {"overrides": {"amenity=fountain": {"enabled": True}}}) if p["enabled"]]
        self.pc.validate(on, self.pc.Paths().icon_dir)                                       # same tag twice is fine with an id
        theme = "\n".join(self.pc.theme_block([p for p in on if p["value"] == "fountain"]))
        self.assertEqual(theme.count('v="fountain"'), 2)
        self.assertIn('<m k="drinking_water" v="yes">', theme)                               # drinkable one
        self.assertIn("water_blue.svg", theme); self.assertIn("water_brown.svg", theme)
        nodes = "\n".join(self.pc.nodes_block(on, set()))
        self.assertEqual(nodes.count('value="fountain"'), 1)                                 # stored once, from the lowest zoom
        self.assertIn('value="fountain" zoom-appear="14"', nodes)

    def test_same_tag_twice_needs_an_id(self):
        a = {"key": "amenity", "value": "bench", "zoom": 14, "icon": "shape:circle:red"}
        with self.assertRaises(ValueError):
            self.pc.validate([a, dict(a)], self.pc.Paths().icon_dir)
        self.pc.validate([a, dict(a, id="amenity=bench+x")], self.pc.Paths().icon_dir)

    def test_validate_rejects_bad_values(self):
        icons = self.pc.Paths().icon_dir
        ok = {"key": "amenity", "value": "bench", "zoom": 14, "icon": "shape:circle:red"}
        self.pc.validate([ok], icons)
        for bad in ({"key": 'a"b'}, {"value": "x y"}, {"zoom": 3}, {"zoom": "14"}, {"size": 5},
                    {"icon": "nothing.svg"}, {"icon": "shape:star:red"}, {"icon": "../x.svg"}):
            with self.assertRaises(ValueError, msg=str(bad)):
                self.pc.validate([dict(ok, **bad)], icons)

    def test_prepare_tree_leaves_repo_untouched_and_writes_icons(self):
        import hashlib
        theme = self.pc.Paths().theme
        before = hashlib.md5(open(theme, "rb").read()).hexdigest()
        user = {"overrides": {"amenity=fuel": {"enabled": False}, "tourism=camp_site": {"enabled": True}},
                "custom": [{"key": "amenity", "value": "bench", "zoom": 14, "icon": "shape:triangle:purple", "size": 120}]}
        pois = [p for p in self.pc.effective_pois(self.base, user) if p.get("enabled", True)]
        paths = self.pc.prepare_tree(os.path.join(self.tmp.name, "gen"), pois)
        self.assertEqual(hashlib.md5(open(theme, "rb").read()).hexdigest(), before)
        text = open(paths.theme, encoding="utf-8").read()
        self.assertIn('v="bench"', text)
        self.assertNotIn('v="fuel"', text)
        self.assertTrue((paths.icon_dir / "shape-triangle-purple.svg").is_file())
        self.assertIn("bench", open(paths.tags_to_keep, encoding="utf-8").read())

    def test_only_if_whitelist(self):
        icons = self.pc.Paths().icon_dir
        shelter = [p for p in self.base if p["value"] == "shelter"][0]
        self.assertNotIn("public_transport", shelter["only_if"]["shelter_type"])
        self.pc.validate([shelter], icons)
        self.assertIn(("shelter_type", "weather_shelter"), self.pc.sub_tags(shelter))
        block = "\n".join(self.pc.theme_block([shelter]))
        self.assertIn('<m k="shelter_type" v="weather_shelter|', block)
        self.assertIn('<m k="access" v="-|customers|private">', block)
        with self.assertRaises(ValueError):
            self.pc.validate([dict(shelter, only_if={"shelter_type": []})], icons)

    def test_poi_args(self):
        old = settings.SETTINGS_PATH
        settings.SETTINGS_PATH = os.path.join(self.tmp.name, "settings.json")
        try:
            self.assertEqual(jobs.poi_args({"pois": {"overrides": {}, "custom": []}}), [])
            args = jobs.poi_args({"pois": {"overrides": {"amenity=fuel": {"enabled": False}}, "custom": []}})
            self.assertEqual(args[0], "--poi-rules")
            data = json.load(open(args[1]))
            self.assertNotIn("amenity=fuel", [self.pc.poi_id(p) for p in data["pois"]])
            with self.assertRaises(jobs.SettingsError):
                jobs.poi_args({"pois": {"overrides": {}, "custom": [{"key": "a b", "value": "x", "zoom": 14, "icon": "shape:circle:red"}]}})
        finally:
            settings.SETTINGS_PATH = old

    def test_catalog_and_icons(self):
        cat = server.poi_catalog()
        ids = [p["id"] for p in cat["pois"]]
        self.assertIn("amenity=bicycle_repair_station", ids)
        self.assertIn("tourism=camp_site", ids)
        self.assertIn("circle", cat["shapes"])
        toilets = [p for p in cat["pois"] if p["id"] == "amenity=toilets"][0]
        self.assertEqual([v["label"] for v in toilets["variants"]], ["paid", "free"])
        self.assertTrue(cat["icons"])

    def test_settings_keep_free_form_poi_choices(self):
        p = os.path.join(self.tmp.name, "s.json")
        s = settings.load(p)
        s["pois"] = {"overrides": {"amenity=fuel": {"zoom": 12}}, "custom": []}
        s["ui"] = {"sections": {"pois": "simple"}}
        settings.save(s, p)
        s2 = settings.load(p)
        self.assertEqual(s2["pois"]["overrides"]["amenity=fuel"]["zoom"], 12)
        self.assertEqual(s2["ui"]["sections"]["pois"], "simple")


class LabelNameTests(unittest.TestCase):
    def test_label_file_name(self):
        from wahoondrer.tiles import label_file_name
        self.assertEqual(label_file_name(88, "Munich, Ingolstadt"), "88_Munich-Ingolstadt.txt")
        self.assertEqual(label_file_name(88, "Saint-Étienne, Zürich"), "88_Saint-Etienne-Zurich.txt")
        self.assertIsNone(label_file_name(88, " , "))


class SplitCoveredTests(unittest.TestCase):
    """A tile no OSM region covers (open sea) is left out; the others are still built."""

    class NoRegion(Exception):
        pass

    def tiles_of(self, w):
        if w["x"] < 128 and w["y"] > 90:
            raise self.NoRegion(f"{w['x']}/{w['y']}")
        return [{"x": w["x"], "y": w["y"], "countries": ["bayern"]}]

    def test_valid_and_invalid_tiles_mixed(self):
        from wahoondrer.tiles import split_covered
        wanted = [{"x": 136, "y": 88}, {"x": 100, "y": 95}, {"x": 137, "y": 88}]
        tiles, skipped = split_covered(wanted, self.tiles_of, self.NoRegion)
        self.assertEqual([(t["x"], t["y"]) for t in tiles], [(136, 88), (137, 88)])
        self.assertEqual([w for w, _ in skipped], [{"x": 100, "y": 95}])
        self.assertEqual(str(skipped[0][1]), "100/95")

    def test_all_valid_and_all_invalid(self):
        from wahoondrer.tiles import split_covered
        self.assertEqual(split_covered([{"x": 136, "y": 88}], self.tiles_of, self.NoRegion)[1], [])
        tiles, skipped = split_covered([{"x": 1, "y": 99}, {"x": 2, "y": 99}], self.tiles_of, self.NoRegion)
        self.assertEqual((tiles, len(skipped)), ([], 2))
        self.assertEqual(split_covered([], self.tiles_of, self.NoRegion), ([], []))

    def test_other_errors_are_not_hidden(self):
        from wahoondrer.tiles import split_covered
        def broken(w):
            raise ValueError("bad data")
        with self.assertRaises(ValueError):
            split_covered([{"x": 136, "y": 88}], broken, self.NoRegion)


class ResultsTests(unittest.TestCase):
    def test_results_tree_and_safe_file_access(self):
        with tempfile.TemporaryDirectory() as out:
            maps = os.path.join(out, "wahoondrer_maps", "maps")
            os.makedirs(os.path.join(maps, "tiles", "8", "136"))
            os.makedirs(os.path.join(maps, "vtm-elemnt", "icons"))
            _write(os.path.join(maps, "tiles", "8", "136", "88.map.lzma"), "abc")
            _write(os.path.join(maps, "tiles", "8", "136", "88.map.lzma.17"), "abc")
            _write(os.path.join(maps, "tiles", "8", "136", "88_Munich.txt"), "info")
            _write(os.path.join(maps, "vtm-elemnt", "vtm-elemnt.xml"), "<x/>")
            _write(os.path.join(maps, "vtm-elemnt", "icons", "a.svg"), "<svg/>")
            _write(os.path.join(out, "secret.txt"), "no")
            for junk in (os.path.join(maps, "vtm-elemnt", ".DS_Store"), os.path.join(maps, "vtm-elemnt", "icons", "Thumbs.db"),
                         os.path.join(maps, "tiles", "8", "136", "._88.map.lzma")):
                _write(junk, "x")
            os.makedirs(os.path.join(maps, "tiles", "8", "137"))
            _write(os.path.join(maps, "tiles", "8", "137", "89.map.lzma"), "abcdef")
            _write(os.path.join(out, "last_run.json"), json.dumps({"selected": ["136/88"], "built": ["136/88"]}))
            _write(os.path.join(out, "tiles_info.json"), json.dumps({"136/88": {"contour": "25-75-150", "wandrer": False}}))
            r = server.results(out)
            self.assertEqual(r["tiles"][0]["items"][0]["contour"], "25-75-150")
            self.assertEqual(r["tiles"][1]["items"][0]["contour"], "")
            self.assertTrue(r["tiles"][0]["items"][0]["plain"])
            self.assertFalse(r["tiles"][1]["items"][0]["plain"])
            self.assertEqual([(t["x"], t["last"]) for t in r["tiles"]], [(136, True), (137, False)])
            it = r["tiles"][0]["items"][0]
            self.assertEqual((it["y"], it["new"], it["label"], it["size"]), (88, True, "Munich", 3))
            self.assertGreater(it["mtime"], 0)
            self.assertFalse(r["tiles"][1]["items"][0]["new"])
            self.assertEqual([f["name"] for f in r["theme"]["files"]], ["vtm-elemnt.xml"])
            self.assertEqual(r["theme"]["files"][0]["name"], "vtm-elemnt.xml")
            self.assertEqual(len(r["theme"]["icons"]), 1)
            tile = r["tiles"][0]
            self.assertEqual(tile["labels"], {"88": "Munich"})
            self.assertEqual([f["name"] for f in tile["files"]], ["88.map.lzma", "88.map.lzma.17"])
            self.assertTrue(server.result_file(out, "wahoondrer_maps/maps/tiles/8/136/88.map.lzma"))
            self.assertIsNone(server.result_file(out, "secret.txt"))
            self.assertIsNone(server.result_file(out, "../" + os.path.basename(out) + "/secret.txt"))
            self.assertEqual(server.results(os.path.join(out, "none"))["tiles"], [])
            self.assertFalse(r["theme"]["new"])
            _write(os.path.join(out, "last_run.json"), json.dumps({"selected": [], "built": [], "theme_new": True}))
            self.assertTrue(server.results(out)["theme"]["new"])


class GzTilesTests(unittest.TestCase):
    """.map.gz tiles in Output/wahoondrer_maps_gz/, laid out like the device."""
    def _out(self, d):
        import lzma
        maps = os.path.join(d, "wahoondrer_maps", "maps")
        self.data = b"mapsforge binary OSM" + os.urandom(5000)
        os.makedirs(os.path.join(maps, "tiles", "8", "136"))
        with open(os.path.join(maps, "tiles", "8", "136", "88.map.lzma"), "wb") as f:
            f.write(lzma.compress(self.data))
        _write(os.path.join(maps, "tiles", "8", "136", "88.map.lzma.17"), "")
        _write(os.path.join(maps, "tiles", "8", "136", "88_Munich.txt"), "x")
        os.makedirs(os.path.join(maps, "vtm-elemnt"))
        _write(os.path.join(maps, "vtm-elemnt", "vtm-elemnt.xml"), "<rendertheme/>")
        _write(os.path.join(maps, "vtm-elemnt", ".DS_Store"), "x")
        return maps

    def test_prepare_converts_once(self):
        import gzip
        from wahoondrer import gztiles as roam
        with tempfile.TemporaryDirectory() as d:
            self._out(d)
            self.assertEqual(roam.missing(d), ["136/88"])
            self.assertEqual(roam.prepare(d, log=lambda *_: None), ["136/88"])
            gz = os.path.join(d, "wahoondrer_maps_gz", "maps", "tiles", "8", "136", "88.map.gz")
            with gzip.open(gz, "rb") as f:
                self.assertEqual(f.read(), self.data)
            with open(gz, "rb") as f:                       # the name inside the gzip: 88.map
                self.assertIn(b"88.map\x00", f.read(40))
            self.assertTrue(os.path.isfile(os.path.join(d, "wahoondrer_maps_gz", "maps", "vtm-elemnt", "vtm-elemnt.xml")))
            self.assertFalse(os.path.exists(os.path.join(d, "wahoondrer_maps_gz", "maps", "vtm-elemnt", ".DS_Store")))
            self.assertEqual(os.path.getsize(os.path.join(d, "wahoondrer_maps_gz", roam.CFG)), 0)
            self.assertEqual(roam.missing(d), [])
            self.assertEqual(roam.prepare(d, log=lambda *_: None), [])       # nothing new: nothing redone
            src = os.path.join(d, "wahoondrer_maps", "maps", "tiles", "8", "136", "88.map.lzma")
            os.utime(src, (time.time() + 60, time.time() + 60))               # the tile was made again
            self.assertEqual(roam.missing(d), ["136/88"])

    def test_results_and_files_for_the_roam(self):
        from wahoondrer import gztiles as roam
        with tempfile.TemporaryDirectory() as d:
            self._out(d)
            r = server.results(d, "gz")
            self.assertEqual((r["format"], r["gz"]["missing"], r["gz"]["cfg"], r["tiles"]), ("gz", ["136/88"], None, []))
            roam.prepare(d, log=lambda *_: None)
            r = server.results(d, "gz")
            self.assertEqual(r["gz"]["missing"], [])
            self.assertEqual(r["gz"]["cfg"]["name"], roam.CFG)
            self.assertEqual([f["name"] for f in r["tiles"][0]["files"]], ["88.map.gz"])
            self.assertEqual(r["tiles"][0]["items"][0]["label"], "Munich")            # names taken from wahoondrer_maps
            self.assertTrue(r["theme"]["dir"].endswith(os.path.join("wahoondrer_maps_gz", "maps", "vtm-elemnt")))
            self.assertTrue(server.result_file(d, "wahoondrer_maps_gz/maps/tiles/8/136/88.map.gz"))
            self.assertEqual([f["name"] for f in server.results(d)["tiles"][0]["files"]], ["88.map.lzma", "88.map.lzma.17"])

    def test_copy_command(self):
        s = CommandTests.s(CommandTests())
        argv = jobs.copy_command(s)[1]
        self.assertEqual(argv[1:3], ["-m", "wahoondrer.devcopy"])
        self.assertEqual(argv[argv.index("--format") + 1], "lzma")
        self.assertNotIn("--adb", argv)
        s["tile_format"] = "gz"; s["tools"]["adb"] = "/opt/adb"
        argv = jobs.copy_command(s)[1]
        self.assertEqual((argv[argv.index("--format") + 1], argv[argv.index("--adb") + 1]), ("gz", "/opt/adb"))
        s["folders"]["out"] = ""
        with self.assertRaises(jobs.SettingsError):
            jobs.copy_command(s)

    def test_commands_and_setting(self):
        self.assertEqual(settings.tile_format({}), "lzma")
        self.assertEqual(settings.tile_format({"tile_format": "gz"}), "gz")
        self.assertEqual(settings.tile_format({"tile_format": "zip"}), "lzma")
        with tempfile.TemporaryDirectory() as d:                 # the model menu of the previous version
            p = os.path.join(d, "s.json"); _write(p, json.dumps({"wahoo_model": "roam1"}))
            self.assertEqual(settings.load(p)["tile_format"], "gz")
        s = CommandTests.s(CommandTests())
        self.assertNotIn("wahoondrer.gztiles", str(jobs.build_commands(s, ["/x/a.kmz"], None)))
        s["tile_format"] = "gz"
        cmds = jobs.build_commands(s, ["/x/a.kmz"], None)
        self.assertEqual(cmds[-1][1][1:3], ["-m", "wahoondrer.gztiles"])
        self.assertEqual(cmds[-1][1][cmds[-1][1].index("--tiles") + 1], "135/88,136/88")
        self.assertNotIn("wahoondrer.gztiles", str(jobs.build_commands(s, [], None, plan=True)))
        s["device"] = "garmin"
        self.assertNotIn("wahoondrer.gztiles", str(jobs.build_commands(s, ["/x/a.kmz"], None)))


class ProgressTests(unittest.TestCase):
    def test_download_and_steps(self):
        import contextlib, http.server, io, threading
        from wahoondrer import progress
        data = os.urandom(3_000_000)
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers()
                self.wfile.write(data)
            def log_message(self, *a): pass
        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, "x.osm.pbf")
            buf = io.StringIO()
            with mock.patch.dict(os.environ, {"WAHOONDRER_GUI": "1"}), contextlib.redirect_stdout(buf):
                progress.download(f"http://127.0.0.1:{srv.server_port}/x", target)
                st = progress.Steps(2); st.run("One", lambda: None); st.total = 1; st.done()
            self.assertEqual(os.path.getsize(target), len(data))
            self.assertFalse(os.path.exists(target + ".part"))        # renamed once complete
        srv.shutdown()
        out = buf.getvalue().splitlines()
        self.assertTrue(any(l.startswith("\x01P|x.osm.pbf") for l in out))
        self.assertIn("\x01P|", out)                                  # bar cleared at the end
        self.assertIn("\x01S|1|2|One", out)
        self.assertIn("\x01S|1|1|", out)
        self.assertIn("\x01P|x.osm.pbf  connecting...", out)
        self.assertEqual(progress.bar(0.5, 4), "██░░")

    def test_incomplete_download_leaves_no_final_file(self):
        import contextlib, http.server, io, threading
        from wahoondrer import progress
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200); self.send_header("Content-Length", "1000000"); self.end_headers()
                self.wfile.write(b"x" * 1000)            # connection closes after 1000 bytes
            def log_message(self, *a): pass
        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, "y.osm.pbf")
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises((SystemExit, Exception)):
                    progress.download(f"http://127.0.0.1:{srv.server_port}/y", target)
            self.assertFalse(os.path.exists(target))
        srv.shutdown()


if __name__ == "__main__":
    unittest.main()
