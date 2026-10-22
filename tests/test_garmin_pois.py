import os
import shutil
import subprocess
import tempfile
import unittest

from wahoondrer import poi_config
from wahoondrer.garmin import build, look, pois, style, typ

JAR = os.environ.get("MKGMAP_JAR", "")

TOILETS = {"key": "amenity", "value": "toilets", "zoom": 14, "icon": "toilets_blue.svg", "size": 150,
           "hide_if": {"access": ["customers", "private"]},
           "variants": [{"tag": "fee", "value": "yes", "icon": "toilets_red.svg", "label": "paid"},
                        {"tag": "fee", "value": "no", "icon": "toilets_green.svg", "label": "free"}]}
FOUNTAIN = {"id": "amenity=fountain+drinkable", "key": "amenity", "value": "fountain", "zoom": 14,
            "icon": "water_blue.svg", "only_if": {"drinking_water": ["yes"]}}
CAFE = {"key": "amenity", "value": "cafe", "zoom": 15, "icon": "cafe.svg"}

try:
    import cairosvg  # noqa: F401
    HAS_CAIRO = True
except (ImportError, OSError):
    HAS_CAIRO = False


class PlanTest(unittest.TestCase):
    def test_variants_first_then_default_without_variant_values(self):
        rules, icons = pois.plan([TOILETS])
        conds = [c for c, _, _ in rules]
        self.assertEqual(conds, [
            "amenity=toilets & access!=customers & access!=private & fee=yes",
            "amenity=toilets & access!=customers & access!=private & fee=no",
            "amenity=toilets & access!=customers & access!=private & fee!=yes & fee!=no"])
        self.assertEqual(len(icons), 3)

    def test_variant_switched_off_is_not_drawn_and_does_not_fall_back(self):
        p = dict(TOILETS, variants=[dict(TOILETS["variants"][0], enabled=False), TOILETS["variants"][1]])
        conds = [c for c, _, _ in pois.plan([p])[0]]
        self.assertFalse(any(c.endswith("fee=yes") for c in conds))
        self.assertIn("fee!=yes", conds[-1])

    def test_default_switched_off(self):
        rules, _ = pois.plan([dict(TOILETS, default_enabled=False)])
        self.assertEqual(len(rules), 2)

    def test_only_if_and_several_values(self):
        cond = pois.plan([FOUNTAIN])[0][0][0]
        self.assertEqual(cond, "amenity=fountain & drinking_water=yes")
        cond = pois.plan([dict(FOUNTAIN, only_if={"drinking_water": ["yes", "treated"]})])[0][0][0]
        self.assertEqual(cond, "amenity=fountain & (drinking_water=yes | drinking_water=treated)")

    def test_same_icon_same_size_shares_a_type(self):
        rules, icons = pois.plan([FOUNTAIN, dict(FOUNTAIN, id="x", value="drinking_water", only_if={})])
        self.assertEqual(len(icons), 1)
        self.assertEqual(rules[0][1], rules[1][1])

    def test_zoom_order_is_kept(self):
        (_, _, r15), = pois.plan([CAFE])[0]
        (_, _, r14), = pois.plan([FOUNTAIN])[0]
        self.assertGreater(r15, r14)
        self.assertEqual(pois.resolution(99), pois.RES_MAX)
        self.assertEqual(pois.resolution(0), pois.RES_MIN)

    def test_icon_size_follows_the_percentage(self):
        self.assertEqual(pois.icon_px(CAFE), 24)
        self.assertEqual(pois.icon_px(dict(CAFE, size=150)), 36)
        self.assertEqual(pois.icon_px(dict(CAFE, size=400)), pois.ICON_MAX_PX)

    def test_too_many_icons(self):
        shapes = [f"shape:{s}:{c}" for s in poi_config.SHAPES for c in poi_config.COLORS]
        many = [dict(CAFE, value=f"v{i}", icon=icon) for i, icon in enumerate(shapes[:pois.MAX_ICONS + 1])]
        with self.assertRaises(pois.PoiError):
            pois.plan(many)

    def test_points_file_and_filter(self):
        rules, _ = pois.plan([CAFE, TOILETS])
        text = pois.points_file(rules)
        self.assertTrue(text.startswith("amenity=cafe [0x6401 resolution 22]\n"))
        self.assertEqual(pois.main_tags([CAFE, TOILETS, CAFE]), ["nwr/amenity=cafe", "nwr/amenity=toilets"])

    def test_repository_rules_fit(self):
        rules, icons = pois.plan(pois.load())
        self.assertTrue(rules)
        self.assertLessEqual(len(icons), pois.MAX_ICONS)

    def test_extract_needs_a_file(self):
        with self.assertRaises(pois.PoiError):
            pois.extract([], (0, 0, 1, 1), [CAFE], tempfile.gettempdir())
        with self.assertRaises(pois.PoiError):
            pois.extract(["/nonexistent.osm.pbf"], (0, 0, 1, 1), [CAFE], tempfile.gettempdir())


@unittest.skipUnless(HAS_CAIRO, "cairosvg is needed to draw the POI icons")
class IconTest(unittest.TestCase):
    def test_xpm(self):
        lines = pois.icon_xpm("cafe.svg", 24)
        head = lines[0]
        self.assertTrue(head.startswith('DayXpm="24 24 '))
        ncolors = int(head.split('"')[1].split()[2])
        self.assertLessEqual(ncolors, pois.MAX_COLORS + 1)
        rows = lines[1 + ncolors:]
        self.assertEqual(len(rows), 24)
        self.assertTrue(all(len(r) == 26 for r in rows))     # 24 pixels + quotes
        self.assertIn(".", "".join(rows))                    # transparent corners

    def test_shape_icon(self):
        self.assertTrue(pois.icon_xpm("shape:circle:red", 16)[0].startswith('DayXpm="16 16 '))

    def test_typ_points(self):
        _, icons = pois.plan([CAFE])
        text = typ.typ_text(look.clean({}), points=pois.typ_points(icons))
        self.assertIn("[_point]\nType=0x6401\nString=cafe\nDayXpm=", text)


OSM = """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6">
  <node id="1" lat="48.1500" lon="11.5500"><tag k="amenity" v="toilets"/><tag k="access" v="private"/></node>
  <node id="2" lat="48.1510" lon="11.5520"><tag k="amenity" v="cafe"/><tag k="name" v="Cafe Test"/></node>
</osm>
"""


@unittest.skipUnless(JAR and os.path.isfile(JAR) and HAS_CAIRO,
                     "set MKGMAP_JAR (and install cairosvg) to run the mkgmap POI test")
class MkgmapPoiTest(unittest.TestCase):
    def test_pois_only_map_without_names(self):
        with tempfile.TemporaryDirectory() as d:
            osm = os.path.join(d, "p.osm")
            with open(osm, "w") as f:
                f.write(OSM)
            rules, icons = pois.plan([TOILETS, CAFE])
            out = os.path.join(d, "g.img")
            build.build_img(osm, out, JAR, poi_rules=pois.points_file(rules), poi_typ=pois.typ_points(icons),
                            log=lambda *_: None)
            with open(out, "rb") as f:
                self.assertNotIn(b"Cafe Test", f.read())


@unittest.skipUnless(shutil.which("osmium"), "osmium is needed for the extract test")
class ExtractTest(unittest.TestCase):
    def test_cut_and_filter(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "src.osm")
            with open(src, "w") as f:
                f.write(OSM.replace('<node id="2" lat="48.1510"', '<node id="2" lat="49.5"'))
            pbf = os.path.join(d, "src.osm.pbf")
            subprocess.run(["osmium", "cat", src, "-o", pbf], check=True)
            got = pois.extract([pbf], (11.5, 48.1, 11.6, 48.2), [TOILETS, CAFE], os.path.join(d, "w"),
                               log=lambda *_: None)
            txt = subprocess.run(["osmium", "cat", got, "-f", "opl"], capture_output=True, text=True).stdout
            self.assertIn("amenity=toilets", txt)
            self.assertNotIn("amenity=cafe", txt)      # outside the box


if __name__ == "__main__":
    unittest.main()
