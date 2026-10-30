import math
import os
import shutil
import struct
import tempfile
import unittest

from wahoondrer.garmin import build, look, style, typ

JAR = os.environ.get("MKGMAP_JAR", "")


class LookTest(unittest.TestCase):
    def test_default_is_complete(self):
        c = look.clean({})
        self.assertEqual(c["day"]["unvisited"], {"color": "#00E1FF", "width": 4, "edge": True, "dash": None})
        self.assertEqual(c["day"]["visited"], {"color": "#FF00FF", "width": 4, "edge": False, "dash": None})
        self.assertEqual(c["night"], c["day"])

    def test_partial_override_and_night_follows_day(self):
        c = look.clean({"day": {"unvisited": {"color": "#00ff00", "width": 3}}})
        self.assertEqual(c["day"]["unvisited"]["color"], "#00FF00")
        self.assertEqual(c["night"]["unvisited"]["width"], 3)
        self.assertEqual(c["day"]["visited"]["color"], "#FF00FF")

    def test_night_changes_colour_only(self):
        c = look.clean({"day": {"unvisited": {"width": 3}},
                        "night": {"unvisited": {"color": "#112233", "width": 7, "edge": False}}})
        self.assertEqual(c["night"]["unvisited"], {"color": "#112233", "width": 3, "edge": True, "dash": None})

    def test_bad_values(self):
        for bad in ({"day": {"unvisited": {"color": "red"}}},
                    {"day": {"unvisited": {"width": 0}}},
                    {"day": {"unvisited": {"width": 2.5}}},
                    {"day": {"visited": {"width": 99}}}):
            with self.assertRaises(ValueError):
                look.clean(bad)


class StyleTypTest(unittest.TestCase):
    def test_every_line_type_has_a_rule_and_a_typ_element(self):
        text = typ.typ_text(look.clean({}))
        types = (style.TYPE_UNVISITED, style.TYPE_VISITED, style.TYPE_UNVISITED_UNPAVED,
                 style.TYPE_VISITED_UNPAVED, style.TYPE_BOUNDARY, style.TYPE_SQUADRAT_BIG, style.TYPE_SQUADRAT_SMALL)
        self.assertEqual(len(set(types)), len(types))
        for t in types:
            self.assertIn(f"[0x{t:02x} resolution", style.LINES)
            self.assertIn(f"Type=0x{t:02x}\n", text)
            self.assertLessEqual(t, 0x07)

    def test_squadrats_default_look(self):
        c = look.clean({})["day"]
        self.assertEqual(c["squadrat_big"], {"color": "#1E50FF", "width": 2, "edge": False, "dash": None})
        self.assertEqual(c["squadrat_small"]["dash"], [8, 8])
        self.assertIn("squadrat=small [0x02 resolution", style.LINES)
        self.assertEqual(style.TYPE_BOUNDARY, max(t for t in range(1, 8) if f"[0x{t:02x} " in style.LINES))

    def test_unpaved_rules_come_before_the_general_ones(self):
        lines = style.LINES.splitlines()
        self.assertLess(lines.index(next(l for l in lines if "unvisited & surface=unpaved" in l)),
                        lines.index(next(l for l in lines if l.startswith("wandrer=unvisited ["))))

    def test_default_typ_is_the_tested_one(self):
        text = typ.typ_text(look.clean({}))
        self.assertIn("FID=1299\nProductCode=1\n", text)
        self.assertIn('Type=0x06\nString=Unvisited\nLineWidth=4\nBorderWidth=1\nXpm="0 0 2 0"\n"a c #00E1FF"\n"b c #FFFFFF"', text)
        self.assertNotIn("0 0 4 0", text)    # no night colours when night = day

    def test_night_colours_added_only_when_different(self):
        text = typ.typ_text(look.clean({"night": {"unvisited": {"color": "#FF55AA"}}}))
        self.assertIn('Xpm="0 0 4 0"\n"a c #00E1FF"\n"b c #FFFFFF"\n"c c #FF55AA"\n"d c #FFFFFF"', text)
        self.assertNotIn("NightXpm", text)

    def test_unpaved_is_dashed_by_default(self):
        text = typ.typ_text(look.clean({}))
        block = text.split("String=Unvisited (unpaved)\n")[1].split("[end]")[0]
        row = '"' + ("a" * 8 + "." * 8) * 2 + '"\n'
        self.assertEqual(block, 'Xpm="32 4 2 1"\n"a c #00E1FF"\n". c none"\n' + row * 4)

    def test_dash_length_and_solid_option(self):
        c = look.clean({"day": {"visited_unpaved": {"dash": [12, 4], "width": 3},
                                "unvisited_unpaved": {"dash": None, "width": 2}}})
        text = typ.typ_text(c)
        block = text.split("String=Visited (unpaved)\n")[1].split("[end]")[0]
        self.assertTrue(block.startswith('Xpm="32 3 2 1"'))
        self.assertEqual(block.count('"' + "a" * 12 + "...." + "a" * 12 + "...." + '"'), 3)
        block = text.split("String=Unvisited (unpaved)\n")[1].split("[end]")[0]
        self.assertEqual(block, 'LineWidth=2\nXpm="0 0 1 0"\n"a c #00E1FF"\n')
        self.assertEqual(c["day"]["visited"]["color"], "#FF00FF")

    def test_bad_dashes(self):
        for dash in ([5, 2], [0, 8], [8, 0], "x", [1]):
            with self.assertRaises(ValueError):
                look.clean({"day": {"visited_unpaved": {"dash": dash}}})
        with self.assertRaisesRegex(ValueError, "edge"):
            look.clean({"day": {"visited_unpaved": {"edge": True}}})

    def test_boundary_default(self):
        self.assertEqual(look.clean({})["day"]["boundary"],
                         {"color": "#B000FF", "width": 8, "edge": False, "dash": None})

    def test_write_style(self):
        with tempfile.TemporaryDirectory() as d:
            style.write_style(d)
            self.assertEqual(sorted(os.listdir(d)), ["info", "lines", "points", "polygons", "version"])


def fake_img(subfiles, continuation=0):
    """A minimal .img container laid out as mkgmap does: an empty block, the blank FAT entry (size = where
    the data starts), then one entry per sub-file; the first sub-file gets `continuation` extra entries
    (part 1, 2...) like a large RGN. Followed by data blocks that look like FAT entries."""
    entries = []
    for i, (name, size) in enumerate(subfiles.items()):
        entries.append((name, size, 0))
        if i == 0:
            entries += [(name, 0, part) for part in range(1, continuation + 1)]
    n = len(entries) + 2
    data_start = 0x200 + 512 * n
    d = bytearray(data_start + 512 * 2)
    d[0x10:0x17] = b"DSKIMG\0"

    def entry(at, base, ext, size, part):
        d[at] = 1
        d[at + 1:at + 9] = base.ljust(8).encode()
        d[at + 9:at + 12] = ext.ljust(3).encode()
        d[at + 12:at + 16] = struct.pack("<I", size)
        d[at + 0x10:at + 0x12] = struct.pack("<H", part << 8)

    entry(0x400, "", "", data_start, 3)
    for i, (name, size, part) in enumerate(entries):
        base, ext = name.split(".")
        entry(0x600 + 512 * i, base, ext, size, part)
    entry(data_start, "JUNK", "RGN", 0, 0)    # data that happens to look like an entry: must be ignored
    return bytes(d)


class CheckImgTest(unittest.TestCase):
    def check(self, subfiles, extra=b"", continuation=0):
        with tempfile.NamedTemporaryFile(suffix=".img", delete=False) as f:
            f.write(fake_img(subfiles, continuation) + extra)
        try:
            return build.check_img(f.name)
        finally:
            os.unlink(f.name)

    def test_ok(self):
        self.assertIn("A.RGN", self.check({"A.RGN": 5000, "A.TYP": 300}))

    def test_large_rgn_over_several_entries(self):
        subs = self.check({"A.RGN": 604219, "A.TRE": 8013, "A.TYP": 310}, continuation=4)
        self.assertEqual(subs["A.RGN"], 604219)
        self.assertNotIn("JUNK.RGN", subs)

    def test_empty_map(self):
        with self.assertRaisesRegex(build.GarminError, "empty"):
            self.check({"A.RGN": 125, "A.TYP": 300})

    def test_missing_typ(self):
        with self.assertRaisesRegex(build.GarminError, "no TYP"):
            self.check({"A.RGN": 5000})

    def test_uncompiled_typ(self):
        with self.assertRaisesRegex(build.GarminError, "compiled"):
            self.check({"A.RGN": 5000, "A.TYP": 300}, extra=b"[_id]\nFID=1299")

    def test_not_an_img(self):
        with tempfile.NamedTemporaryFile(suffix=".img", delete=False) as f:
            f.write(b"x" * 1024)
        try:
            with self.assertRaises(build.GarminError):
                build.check_img(f.name)
        finally:
            os.unlink(f.name)


OSM = """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6">
  <node id="1" lat="48.1500" lon="11.5500"/><node id="2" lat="48.1510" lon="11.5520"/>
  <node id="3" lat="48.1520" lon="11.5540"/><node id="4" lat="48.1530" lon="11.5560"/>
  <way id="10"><nd ref="1"/><nd ref="2"/><tag k="wandrer" v="unvisited"/></way>
  <way id="11"><nd ref="3"/><nd ref="4"/><tag k="wandrer" v="visited"/></way>
</osm>
"""


class SquadratsTest(unittest.TestCase):
    def test_outlines_written_without_wandrer_export(self):
        from tests.synthetic import write_squadrats_kml
        from wahoondrer.process import Options
        with tempfile.TemporaryDirectory() as d:
            kml = os.path.join(d, "s.kml")
            write_squadrats_kml(kml)
            bbox = (11.45, 48.10, 11.55, 48.20)
            lines = build.squadrat_lines(kml, bbox, log=lambda *_: None)
            self.assertTrue(lines["big"] and lines["small"])
            far = build.squadrat_lines(kml, bbox, area=(40.0, 0.0, 1), log=lambda *_: None)
            self.assertEqual(far, {"big": [], "small": []})
            osm = os.path.join(d, "s.osm")
            build.wandrer_osm([], osm, Options(), squadrats=lines, log=lambda *_: None)
            with open(osm) as f:
                text = f.read()
            self.assertIn('k="squadrat" v="big"', text)
            self.assertIn('k="squadrat" v="small"', text)
            self.assertNotIn('k="wandrer"', text)

    def test_lines_are_cut_to_the_area(self):
        lines = [[(10.0, 48.0), (12.0, 48.0)], [(11.0, 47.0), (11.0, 49.0)], [(10.0, 50.0), (12.0, 50.0)]]
        self.assertEqual(build._clip_straight(lines, (10.5, 47.5, 11.5, 48.5)),
                         [[(10.5, 48.0), (11.5, 48.0)], [(11.0, 47.5), (11.0, 48.5)]])
        inner = [[(10.0, 48.0), (10.6, 48.0), (11.0, 48.0), (11.4, 48.0), (12.0, 48.0)]]   # inner nodes kept
        self.assertEqual(build._clip_straight(inner, (10.5, 47.5, 11.5, 48.5)),
                         [[(10.5, 48.0), (10.6, 48.0), (11.0, 48.0), (11.4, 48.0), (11.5, 48.0)]])

    def test_nothing_given(self):
        from wahoondrer.process import Options
        with self.assertRaises(build.GarminError):
            build.wandrer_osm([], os.path.join(tempfile.gettempdir(), "x.osm"), Options(), log=lambda *_: None)


@unittest.skipUnless(JAR and os.path.isfile(JAR), "set MKGMAP_JAR to the mkgmap jar to run the mkgmap test")
class MkgmapTest(unittest.TestCase):
    def test_build(self):
        with tempfile.TemporaryDirectory() as d:
            osm = os.path.join(d, "t.osm")
            with open(osm, "w") as f:
                f.write(OSM)
            out = os.path.join(d, "gmapsupp.img")
            build.build_img(osm, out, JAR, log=lambda *_: None)
            self.assertTrue(build.check_img(out))

    def test_nothing_matches(self):
        with tempfile.TemporaryDirectory() as d:
            osm = os.path.join(d, "t.osm")
            with open(osm, "w") as f:
                f.write(OSM.replace("wandrer", "other"))
            with self.assertRaises(build.GarminError):
                build.build_img(osm, os.path.join(d, "g.img"), JAR, log=lambda *_: None)


class UnexploredSquaresTest(unittest.TestCase):
    """Every unexplored square of the tiles by default; the band and explored-big choices stay available."""
    def brute(self, explored, level, tiles):
        from wahoondrer import squadrats as sq
        z = sq.LEVELS[level][1]; n = 2 ** (z - 8)
        cells = {(x, y) for tx, ty in tiles for x in range(tx * n, (tx + 1) * n) for y in range(ty * n, (ty + 1) * n)}
        h, v = {}, {}
        for x, y in cells - explored:
            for e in ((x, y), (x, y + 1)):
                h.setdefault(e[1], set()).add(e[0])
            for e in ((x, y), (x + 1, y)):
                v.setdefault(e[0], set()).add(e[1])
        return sq._merged_lines({k: list(x) for k, x in h.items()}, {k: list(x) for k, x in v.items()}, z)

    def test_all_is_every_unexplored_square_merged(self):
        import random
        from wahoondrer import squadrats as sq
        random.seed(3)
        tiles = {(136, 88), (137, 88), (136, 89)}                     # shared borders between tiles
        cells = [(x, y) for tx, ty in tiles for x in range(tx * 64, tx * 64 + 64) for y in range(ty * 64, ty * 64 + 64)]
        explored = set(random.sample(cells, 4000))
        norm = lambda ls: sorted(tuple(round(c, 7) for p in l for c in p) for l in ls)
        got = sq.unexplored_outlines(explored, "big", 3, tiles)
        self.assertEqual(norm(got), norm(self.brute(explored, "big", tiles)))
        self.assertEqual(len(norm(got)), len(set(norm(got))))           # no line twice

    def test_a_tile_never_ridden_is_a_plain_grid(self):
        from wahoondrer import squadrats as sq
        lines = sq.unexplored_outlines(set(), "big", 3, {(136, 88)})
        self.assertEqual(len(lines), 2 * 65)                             # 65 long lines in each direction
        self.assertEqual(sq.unexplored_outlines(set(), "big", 3, {(136, 88)}, scope="band"), [])

    def test_a_node_at_every_big_square_corner(self):
        from wahoondrer import squadrats as sq
        big = sq.unexplored_outlines(set(), "big", 3, {(136, 88)})
        self.assertTrue(all(len(line) == 65 for line in big))          # 64 squares: a node at each of the 65 edges
        small = sq.unexplored_outlines(set(), "small", 3, {(136, 88)})
        self.assertTrue(all(len(line) == 65 for line in small))        # 512 small squares, a node every 8
        self.assertEqual(sq._stops(3, 21, 8), [3, 8, 16, 21])

    @unittest.skipUnless(shutil.which("osmium"), "needs osmium")
    def test_a_line_crossing_a_tile_of_the_area_grid_is_kept(self):
        from wahoondrer import squadrats as sq
        from wahoondrer.garmin import tiling
        from wahoondrer.osmxml import write_osm
        n = 2 ** 14
        x = int((11.555 + 180) / 360 * n)                                 # one column of big squares, 48.0 to 48.4 N
        lat_y = lambda lat: int((1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n)
        ys = list(range(lat_y(48.4), lat_y(48.0)))
        line = sq._merged_lines({}, {x: ys}, 14)[0]
        with tempfile.TemporaryDirectory() as d:
            osm = os.path.join(d, "sq.osm")
            write_osm(osm, {}, {}, squadrats={"big": [line]})
            box = (line[0][0] - 0.001, line[-1][1], line[0][0] + 0.001, line[0][1])   # one column of 4-5 cells
            cells = tiling.split(osm, box, d)
            self.assertEqual(len(cells), len(tiling.grid(box)))            # the line is in every row of cells
        from wahoondrer import squadrats as sq
        with self.assertRaises(ValueError):
            sq.unexplored_outlines(set(), "big", 3, {(136, 88)}, scope="explored-big")
        explored = {(136 * 64 + 5, 88 * 64 + 5)}
        band = sq.unexplored_outlines(explored, "big", 1, None)          # no tiles: "all" falls back to the band
        self.assertEqual(band, sq.unexplored_outlines(explored, "big", 1, None, scope="band"))
        self.assertTrue(band)


if __name__ == "__main__":
    unittest.main()
