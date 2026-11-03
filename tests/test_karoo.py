import os
import shutil
import tempfile
import unittest
import xml.dom.minidom

from wahoondrer.karoo import mapfile, theme

OSMOSIS = os.environ.get("OSMOSIS", "")
DEVICE_THEME = ('<?xml version="1.0" encoding="UTF-8"?>\n<rendertheme xmlns="http://mapsforge.org/renderTheme" '
                'version="1">\n    <m e="way" k="highway"><line stroke="#ffffff" width="1"/></m>\n</rendertheme>\n')
TOILETS = {"key": "amenity", "value": "toilets", "zoom": 14, "icon": "toilets_blue.svg",
           "hide_if": {"access": ["customers", "private"]},
           "variants": [{"tag": "fee", "value": "yes", "icon": "toilets_red.svg"},
                        {"tag": "fee", "value": "no", "icon": "toilets_green.svg"}]}
REPAIR = {"key": "amenity", "value": "bicycle_repair_station", "zoom": 14, "icon": "shape:triangle:blue"}


class BlockTest(unittest.TestCase):
    def test_lines_fixed_widths_zooms_and_colours(self):
        b = theme.block(look={"light": {"unvisited": {"color": "#00ff00"}}})
        self.assertIn('<m e="way" k="wandrer" zoom-min="13">', b)
        self.assertIn('stroke="#00FF00" width="2.5" fix="true"', b)
        self.assertIn('<m v="big" zoom-min="11">', b)
        self.assertIn('<m v="small" zoom-min="14">', b)
        self.assertNotIn('v="visited"', b)
        self.assertIn('v="visited"', theme.block(visited=True))
        self.assertNotIn("width=\"1.5\"", b)                              # not the Wahoo widths

    def test_pois_renamed_with_our_icon_folder(self):
        b = theme.block([TOILETS, REPAIR])
        self.assertIn('<m k="maprboy:amenity" v="toilets" zoom-min="14">', b)
        self.assertIn('<m k="fee" v="yes">', b)
        self.assertIn('src="file:/maprboy-icons/toilets_red.svg"', b)
        self.assertIn('src="file:/maprboy-icons/shape-triangle-blue.svg"', b)
        self.assertNotIn('k="amenity"', b)


class ThemeTest(unittest.TestCase):
    def test_added_before_the_end_replaced_and_removed(self):
        once = theme.with_block(DEVICE_THEME, theme.block())
        xml.dom.minidom.parseString(once)
        self.assertLess(once.index(theme.BEGIN), once.index("</rendertheme>"))
        self.assertLess(once.index('k="highway"'), once.index(theme.BEGIN))      # the Karoo's rules are kept, first
        twice = theme.with_block(once, theme.block(visited=True))
        self.assertEqual(twice.count(theme.BEGIN), 1)
        self.assertIn('v="visited"', twice)
        self.assertEqual(theme.without_block(twice), DEVICE_THEME)

    def test_refused(self):
        with self.assertRaises(theme.ThemeError):
            theme.with_block("<osm/>", theme.block())
        with self.assertRaises(theme.ThemeError):
            theme.without_block(DEVICE_THEME.replace("</rendertheme>", theme.BEGIN + "</rendertheme>"))

    def test_icons(self):
        d = tempfile.mkdtemp()
        try:
            names = theme.write_icons([TOILETS, REPAIR], d)
            self.assertEqual(names, ["shape-triangle-blue.svg", "toilets_blue.svg", "toilets_green.svg", "toilets_red.svg"])
            self.assertEqual(sorted(os.listdir(d)), names)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TagMappingTest(unittest.TestCase):
    def test_lines_and_renamed_pois(self):
        t = mapfile.tag_mapping([TOILETS])
        xml.dom.minidom.parseString(t)
        self.assertIn('key="maprboy:amenity" value="toilets" zoom-appear="14"', t)
        self.assertIn('key="fee" value="yes" zoom-appear="14" renderable="false"', t)
        self.assertIn('key="squadrat" value="small" zoom-appear="14"', t)
        self.assertNotIn('value="visited"', t)
        self.assertIn('value="visited"', mapfile.tag_mapping([], visited=True))


@unittest.skipUnless(shutil.which("osmium"), "needs osmium")
class PoisTest(unittest.TestCase):
    def osm(self, path, body):
        with open(path, "w") as f:
            f.write('<?xml version="1.0"?><osm version="0.6">' + body + "</osm>")
        return path

    def test_points_buildings_tags_area_and_duplicates(self):
        d = tempfile.mkdtemp()
        try:
            stamp = 'version="1" timestamp="2026-01-01T00:00:00Z"'
            node = (f'<node id="1" {stamp} lat="48.13" lon="11.55"><tag k="amenity" v="toilets"/>'
                    '<tag k="fee" v="no"/><tag k="name" v="WC"/></node>'
                    f'<node id="2" {stamp} lat="49.00" lon="11.55"><tag k="amenity" v="toilets"/></node>')
            square = "".join(f'<node id="{10 + i}" {stamp} lat="{la}" lon="{lo}"/>' for i, (la, lo) in
                             enumerate([(48.140, 11.560), (48.140, 11.562), (48.142, 11.562), (48.142, 11.560)]))
            building = (f'<way id="20" {stamp}><nd ref="10"/><nd ref="11"/><nd ref="12"/><nd ref="13"/><nd ref="10"/>'
                        '<tag k="amenity" v="toilets"/><tag k="building" v="yes"/><tag k="fee" v="yes"/></way>')
            a = self.osm(os.path.join(d, "a.osm"), node + square + building)
            b = self.osm(os.path.join(d, "b.osm"), node)                 # the same toilet in a neighbouring extract
            out = os.path.join(d, "pois.osm")
            count = mapfile.extract_pois([a, b], (11.5, 48.1, 11.6, 48.2), [TOILETS], out, d, log=lambda *x: None)
            self.assertEqual(count, {"toilets": 2})                       # one point, one building; not the one at 49 N
            with open(out) as f:
                text = f.read()
            self.assertIn('<tag k="maprboy:amenity" v="toilets"/><tag k="fee" v="no"/>', text)
            self.assertIn('lat="48.1410000" lon="11.5610000"', text)      # the building's centre
            self.assertNotIn('k="name"', text)
            self.assertNotIn('k="amenity"', text)
        finally:
            shutil.rmtree(d, ignore_errors=True)


@unittest.skipUnless(OSMOSIS and shutil.which("osmium"), "set OSMOSIS (with the MapWriter plugin) to run it")
class MapTest(unittest.TestCase):
    def test_a_long_line_crossing_the_area_is_kept(self):
        d = tempfile.mkdtemp()
        try:
            lines = os.path.join(d, "lines.osm")
            with open(lines, "w") as f:                                    # both ends outside the area
                f.write('<?xml version="1.0"?><osm version="0.6">'
                        '<node id="1" lat="47.0" lon="11.55"/><node id="2" lat="49.0" lon="11.55"/>'
                        '<way id="1"><nd ref="1"/><nd ref="2"/><tag k="squadrat" v="big"/></way></osm>')
            conf = os.path.join(d, "tag.xml")
            with open(conf, "w") as f:
                f.write(mapfile.tag_mapping())
            out = os.path.join(d, "maprboy-test.map")
            mapfile.write_map([lines], (11.5, 48.1, 11.6, 48.2), out, conf, d, OSMOSIS, log=lambda *x: None)
            with open(out, "rb") as f:
                head = f.read(4096)
            self.assertTrue(head.startswith(b"mapsforge binary OSM"))
            self.assertIn(b"squadrat=big", head)                             # declared in the header
            self.assertGreater(os.path.getsize(out), 300)                     # and some data written
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
