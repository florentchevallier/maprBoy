import os
import shutil
import subprocess
import tempfile
import unittest

from wahoondrer.garmin import base, build, pois, tiling

JAR = os.environ.get("MKGMAP_JAR", "")
CAFE = {"key": "amenity", "value": "cafe", "zoom": 15, "icon": "cafe.svg"}
STATION = {"key": "railway", "value": "station", "zoom": 13, "icon": "train_station.svg"}
STOP = {"key": "railway", "value": "stop", "zoom": 13, "icon": "train_station.svg"}

LINES = ("addr:housenumber=* {set mkgmap:execute_finalize_rules=true}\n"
         "highway=* & name=* {set mkgmap:street='${name}'}\n"
         "man_made=breakwater [0x17 resolution 22 default_name 'Breakwater']\n"
         + base.BARRIER_RULE + " [0x17 resolution 24]\n<finalize>\nname=* {name '${name}'}\n")
POLYGONS = "include 'inc/name';\nnatural=water & water=lock [0x4c resolution 22 default_name 'Lock']\n"
POINTS = ("addr:housenumber=* {set mkgmap:execute_finalize_rules=true}\ninclude 'inc/name';\n"
          "place=city & name=* [0x0100 resolution 12]\nplace=village & name=* [0x0900 resolution 22]\n"
          "amenity=cafe [0x2a0e resolution 24]\n"
          "natural=peak {name '${name|def:}'} [0x6616 resolution 24]\n<finalize>\nname=* {name '${name}'}\n")


class StyleTest(unittest.TestCase):
    def test_no_default_names_no_barrier_names_named_areas_whitelisted(self):
        lines, polygons, _ = base.no_labels(LINES, POLYGONS, POINTS)
        self.assertNotIn("default_name", lines + polygons)
        self.assertIn("barrier=ditch {delete name} [0x17 resolution 24]", lines)
        self.assertIn(f"name=* & {base.NAMED_AREAS} {{delete name", polygons)
        self.assertLess(polygons.index("delete name"), polygons.index("include 'inc/name'"))

    def test_street_names_only_on_request(self):
        off, _, _ = base.no_labels(LINES, POLYGONS, POINTS)
        on, _, _ = base.no_labels(LINES, POLYGONS, POINTS, street_names=True)
        self.assertIn("highway=* & name=* {delete name}", off)
        self.assertNotIn("highway=* & name=* {delete name}", on)
        self.assertIn("{set mkgmap:street='${name}'}", off)        # the address search keeps the name

    def test_our_points_replace_the_default_pois_before_naming(self):
        rules, _ = pois.plan(base.base_pois([CAFE]))
        out = base.our_points(POINTS, rules)
        self.assertNotIn("0x2a0e", out)                            # the default cafe is gone
        self.assertIn("place=village", out)
        self.assertIn("natural=peak", out)
        ours = [r for r in out.split("\n") if "{delete name}" in r]
        self.assertEqual(len(ours), 1)
        self.assertIn("resolution 24]", ours[0])                   # 15 + 7 + 3, at most 24
        self.assertLess(out.index(ours[0]), out.index("include 'inc/name'"))
        self.assertLess(out.index("include 'inc/name'"), out.index("<finalize>"))

    def test_poi_shift(self):
        rules, _ = pois.plan(base.base_pois([STATION]))
        self.assertIn("resolution 23]", base.our_points(POINTS, rules))    # 13 + 7 + 3

    def test_stations_only_and_smaller_icons(self):
        kept = base.base_pois([STATION, STOP, CAFE])
        self.assertEqual([(p["key"], p["value"]) for p in kept], [("railway", "station"), ("amenity", "cafe")])
        self.assertEqual(pois.icon_px(kept[0]), 16)                 # 24 px at 65 %

    def test_changed_default_style_is_reported(self):
        with self.assertRaises(build.GarminError):
            base.no_labels("no barrier rule\n", POLYGONS, POINTS)
        with self.assertRaises(build.GarminError):
            base.our_points("nothing here\n", [])


class GridTest(unittest.TestCase):
    def test_cells_cover_the_box_and_stay_inside(self):
        box = (11.30, 48.00, 11.80, 48.30)
        cells = tiling.grid(box)
        self.assertTrue(all(box[0] <= w < e <= box[2] and box[1] <= s < n <= box[3] for w, s, e, n in cells))
        area = sum((e - w) * (n - s) for w, s, e, n in cells)
        self.assertAlmostEqual(area, (box[2] - box[0]) * (box[3] - box[1]), places=9)

    def test_small_box_one_cell(self):
        self.assertEqual(tiling.grid((11.50, 48.10, 11.55, 48.15)), [(11.50, 48.10, 11.55, 48.15)])

    def test_parse_bbox(self):
        self.assertEqual(tiling.parse_bbox("11.2,47.9,11.9,48.3"), (11.2, 47.9, 11.9, 48.3))
        for bad in ("11.9,47.9,11.2,48.3", "1,2,3", "a,b,c,d"):
            with self.assertRaises(ValueError):
                tiling.parse_bbox(bad)


@unittest.skipUnless(shutil.which("osmium"), "needs osmium")
class SplitTest(unittest.TestCase):
    def test_empty_cells_are_left_out(self):
        tmp = tempfile.mkdtemp()
        try:
            osm = os.path.join(tmp, "w.osm")
            with open(osm, "w") as f:      # one short way near 11.45 E: only its cell is kept
                f.write('<?xml version="1.0"?><osm version="0.6">'
                        '<node id="50000000000" lat="48.13" lon="11.45"/><node id="50000000001" lat="48.13" lon="11.46"/>'
                        '<way id="50000000002"><nd ref="50000000000"/><nd ref="50000000001"/>'
                        '<tag k="wandrer" v="unvisited"/></way></osm>')
            cells = tiling.split(osm, (11.35, 48.10, 11.70, 48.16), tmp)
            self.assertEqual(len(tiling.grid((11.35, 48.10, 11.70, 48.16))), 3)
            self.assertEqual(len(cells), 1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_regions_of_different_dates_merge(self):
        tmp = tempfile.mkdtemp()
        try:
            def region(name, nodes, way):
                p = os.path.join(tmp, name)
                with open(p, "w") as f:
                    f.write('<?xml version="1.0"?><osm version="0.6">' + nodes + way + "</osm>")
                return p
            old = region("old.osm",
                         '<node id="1" version="1" timestamp="2026-10-15T00:00:00Z" lat="48.13" lon="11.45"/>'
                         '<node id="2" version="1" timestamp="2026-10-15T00:00:00Z" lat="48.13" lon="11.46"/>',
                         '<way id="10" version="1" timestamp="2026-10-15T00:00:00Z"><nd ref="1"/><nd ref="2"/>'
                         '<tag k="highway" v="residential"/></way>')
            new = region("new.osm",           # the same border node and way, edited in between
                         '<node id="1" version="2" timestamp="2026-10-22T00:00:00Z" lat="48.131" lon="11.45"/>'
                         '<node id="2" version="1" timestamp="2026-10-15T00:00:00Z" lat="48.13" lon="11.46"/>',
                         '<way id="10" version="2" timestamp="2026-10-22T00:00:00Z"><nd ref="1"/><nd ref="2"/>'
                         '<tag k="highway" v="tertiary"/></way>')
            cells = base.tiles([old, new], (11.35, 48.10, 11.70, 48.16), tmp, log=lambda *a: None)
            self.assertEqual(len(cells), 1)
            out = subprocess.run(["osmium", "cat", cells[0], "-f", "opl"], capture_output=True, text=True).stdout
            self.assertEqual(out.count("\nw") + out.startswith("w"), 1)
            self.assertIn("highway=tertiary", out)           # the latest version is kept
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


@unittest.skipUnless(JAR and os.path.isfile(JAR) and shutil.which("osmium"),
                     "set MKGMAP_JAR (and have osmium) to run the mkgmap test")
class BuildTest(unittest.TestCase):
    def test_two_tiles_build(self):
        tmp = tempfile.mkdtemp()
        try:
            osm = os.path.join(tmp, "in.osm")
            nodes, ways = [], []
            for i, lon in enumerate((11.40, 11.62)):             # one street in each of two grid cells
                a, b = 2 * i + 1, 2 * i + 2
                nodes += [f'<node id="{a}" version="1" lat="48.13" lon="{lon}"/>',
                          f'<node id="{b}" version="1" lat="48.13" lon="{lon + 0.01}"/>']
                ways.append(f'<way id="{i + 1}" version="1"><nd ref="{a}"/><nd ref="{b}"/>'
                            '<tag k="highway" v="residential"/><tag k="name" v="Teststrasse"/></way>')
            with open(osm, "w") as f:
                f.write('<?xml version="1.0"?><osm version="0.6">' + "".join(nodes + ways) + "</osm>")
            pbf = os.path.join(tmp, "in.osm.pbf")
            subprocess.run(["osmium", "cat", osm, "-o", pbf], check=True)
            out = os.path.join(tmp, "base.img")
            base.build_base([pbf], (11.35, 48.10, 11.70, 48.16), out, JAR, [CAFE], log=lambda *a: None)
            subs, _ = build.read_img(out)
            self.assertGreaterEqual(len([k for k in subs if k.endswith(".RGN")]), 2)
            self.assertTrue(any(k.endswith(".TYP") for k in subs))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
