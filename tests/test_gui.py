import os
import tempfile
import unittest

from tests import synthetic
import json
import time

from wahoondrer.gui import jobs, regions, scan, server, settings


def _write(path, data, mode="w"):
    with open(path, mode) as f:
        f.write(data)


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

    def test_default_is_bike_and_a_newer_type_does_not_hide_it(self):
        s = settings.load(os.devnull); infos = self.infos()
        self.assertEqual(s["activity"], "bike")
        self.assertEqual(server.annotate(s, infos), "bike")
        self.assertEqual([i["active"] for i in infos], [False, True, False, False])
        self.assertIn("other type", infos[0]["why"])

    def test_latest_and_any_are_pro_only(self):
        s = settings.load(os.devnull); s["activity"] = "latest"
        self.assertEqual(server.annotate(s, self.infos()), "bike")          # simple mode: back to bike
        s["mode"] = "pro"
        self.assertEqual(server.annotate(s, self.infos()), "combined")      # newest export's type
        s["activity"] = "any"; s["excluded_exports"] = ["new"]
        infos = self.infos(); server.annotate(s, infos)
        self.assertEqual([i["active"] for i in infos], [False, True, False, False])

    def test_old_auto_setting_becomes_latest(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json"); _write(p, json.dumps({"activity": "auto"}))
            self.assertEqual(settings.load(p)["activity"], "latest")


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

    def test_options_are_passed(self):
        cmds = jobs.build_commands(self.s(wandrer={"boundaries": False, "radius_m": 0,
                                                   "with_traveled": True, "paved_always": True}), ["/x/a.kmz"], None)
        a = cmds[0][1]
        self.assertIn("--paved-always", a); self.assertNotIn("--no-merge", a)
        self.assertIn("--no-boundaries", a); self.assertIn("--with-traveled", a)
        self.assertEqual(a[a.index("--radius-m") + 1], "0")

    def test_contour_flags(self):
        s = self.s(); s["build"] = {"skip_classic": False, "contour": True}
        b = jobs.build_commands(s, [], None)[-1][1]
        self.assertIn("--contour", b); self.assertNotIn("--srtm1", b)
        s["build"]["contour"] = False
        self.assertNotIn("--contour", jobs.build_commands(s, [], None)[-1][1])

    def test_plan_only_builds_tiles_plan(self):
        cmds = jobs.build_commands(self.s(), ["/x/a.kmz"], None, plan=True)
        self.assertEqual(len(cmds), 1); self.assertIn("--plan", cmds[0][1])
        self.assertNotIn("--wandrer", cmds[0][1])

    def test_classic_only_without_wandrer(self):
        cmds = jobs.build_commands(self.s(wandrer={"enabled": False}), ["/x/a.kmz"], None)
        self.assertEqual(len(cmds), 1); self.assertNotIn("--wandrer", cmds[0][1])

    def test_errors(self):
        with self.assertRaises(jobs.SettingsError):      # squadrats without wandrer export
            jobs.build_commands(self.s(squadrats={"enabled": True}), [], "/x/sq.kml")
        with self.assertRaises(jobs.SettingsError):      # squadrats without file
            jobs.build_commands(self.s(squadrats={"enabled": True}), ["/x/a.kmz"], None)
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


class ContourTests(unittest.TestCase):
    def test_command_matches_wmc_settings_and_needs_no_account(self):
        from wahoondrer import contour
        tile = {"left": 11.25, "bottom": 47.98, "right": 12.65, "top": 48.92}
        c = contour.build_command("/x/pyhgtmap", tile, "/t/elevation", "/h", 3)
        self.assertEqual(c[0], "/x/pyhgtmap")
        self.assertEqual(c[c.index("-a") + 1], "11.25:47.98:12.65:48.92")
        self.assertEqual(c[c.index("-o") + 1], "/t/elevation")
        for opt in ("--sources=view1,view3", "--jobs=3", "--max-nodes-per-tile=0",
                    "--no-zero-contour", "--hgtdir=/h", "--start-node-id=20000000000"):
            self.assertIn(opt, c)
        self.assertEqual(c[c.index("-c") + 1], "100,50")
        self.assertFalse([a for a in c if "earthexplorer" in a or "srtm" in a])

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


if __name__ == "__main__":
    unittest.main()
