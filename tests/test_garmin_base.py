import os
import shutil
import subprocess
import tempfile
import unittest

from wahoondrer.garmin import base, build, pois

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
        cells = base.grid(box)
        self.assertTrue(all(box[0] <= w < e <= box[2] and box[1] <= s < n <= box[3] for w, s, e, n in cells))
        area = sum((e - w) * (n - s) for w, s, e, n in cells)
        self.assertAlmostEqual(area, (box[2] - box[0]) * (box[3] - box[1]), places=9)

    def test_small_box_one_cell(self):
        self.assertEqual(base.grid((11.50, 48.10, 11.55, 48.15)), [(11.50, 48.10, 11.55, 48.15)])


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
