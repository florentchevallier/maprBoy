"""Merging several Wandrer exports (wahoondrer/kmz.py): when does a newer export
remove a segment of an older one?

Rule under test: only when the newer export REPLACES it (it holds roads lying on
the old segment). Being absent, even inside the newer export's boundaries or
bounding box, is not enough: an export does not describe everything its
boundaries cover.
"""
import os
import tempfile
import unittest

from tests import synthetic
from wahoondrer.kmz import Export, Segment, _key, merge_exports, read_export

LON, LAT = 11.50, 48.15
D = 0.002            # about 148 m east-west at this latitude
M = 1 / 110540.0     # one metre of latitude, in degrees


def line(x0, y0, x1, y1, n=1):
    """Straight line from (x0, y0) to (x1, y1), in degrees, split into n points."""
    return [(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n) for i in range(n + 1)]


def road(x, y, length=D, dy=0.0):
    """East-west road starting at (LON + x, LAT + y), shifted by dy degrees."""
    return line(LON + x, LAT + y + dy, LON + x + length, LAT + y + dy, 4)


def export(name, stamp, segs, activity="bike", traveled=True, untraveled=True, bbox=None, boundaries=None):
    """segs: list of (coords, state[, unpaved])."""
    e = Export(name=name, activity=activity, timestamp=stamp,
               has_traveled=traveled, has_untraveled=untraveled)
    xs, ys = [], []
    for item in segs:
        coords, state = item[0], item[1]
        e.segments[_key(coords)] = Segment(coords, state, item[2] if len(item) > 2 else False)
        xs += [p[0] for p in coords]
        ys += [p[1] for p in coords]
    e.bbox = bbox or (min(xs), min(ys), max(xs), max(ys))
    for ring in boundaries or []:
        e.boundaries[_key(ring)] = ring
    return e


class MergeTests(unittest.TestCase):
    def test_newest_export_wins_on_same_road(self):
        r = road(0, 0)
        old = export("old", "20260901-10:00:00", [(r, "unvisited")])
        new = export("new", "20260902-10:00:00", [(r, "visited")])
        segs, _, rep = merge_exports([old, new])
        self.assertEqual([s.state for s in segs.values()], ["visited"])
        self.assertEqual(rep[1]["state_changed"], 1)

    def test_order_comes_from_timestamp_not_given_order(self):
        r = road(0, 0)
        old = export("old", "20260901-10:00:00", [(r, "unvisited")])
        new = export("new", "20260902-10:00:00", [(r, "visited")])
        segs, _, _ = merge_exports([new, old])
        self.assertEqual([s.state for s in segs.values()], ["visited"])

    def test_absent_road_is_kept_even_inside_newer_bbox_and_boundaries(self):
        """The reported bug: a newer export whose boundaries cover a big area but which
        only holds roads in a small part of it must not delete the others."""
        a, b, c = road(0, 0), road(0, 0.01), road(0, 0.02)
        old = export("old", "20260901-10:00:00", [(a, "unvisited"), (b, "unvisited"), (c, "unvisited")])
        big_ring = line(LON - 1, LAT - 1, LON + 1, LAT - 1) + line(LON + 1, LAT + 1, LON - 1, LAT + 1) + [(LON - 1, LAT - 1)]
        new = export("new", "20260902-10:00:00", [(a, "unvisited"), (road(0, 0.02, dy=M * 400), "unvisited")],
                     boundaries=[big_ring])
        segs, _, rep = merge_exports([old, new])
        self.assertIn(_key(b), segs)       # inside the bbox, inside the boundary, not redrawn
        self.assertIn(_key(c), segs)       # a road 400 m away is not a replacement
        self.assertEqual(rep[1]["removed_stale"], 0)
        self.assertEqual(rep[1]["kept_absent"], 2)

    def test_redrawn_road_is_removed(self):
        """Wandrer redrew the road (split in two after an OSM edit): the old one goes."""
        old_road = road(0, 0)
        half1 = line(LON, LAT, LON + D / 2, LAT, 2)
        half2 = line(LON + D / 2, LAT, LON + D, LAT, 2)
        old = export("old", "20260901-10:00:00", [(old_road, "unvisited")])
        new = export("new", "20260902-10:00:00", [(half1, "unvisited"), (half2, "visited")])
        segs, _, rep = merge_exports([old, new])
        self.assertNotIn(_key(old_road), segs)
        self.assertEqual(len(segs), 2)
        self.assertEqual((rep[1]["added"], rep[1]["removed_stale"], rep[1]["kept_absent"]), (2, 1, 0))

    def test_redrawn_road_with_a_two_metre_shift_is_removed(self):
        old_road = road(0, 0)
        old = export("old", "20260901-10:00:00", [(old_road, "unvisited")])
        new = export("new", "20260902-10:00:00", [(road(0, 0, dy=2 * M), "unvisited")])
        segs, _, rep = merge_exports([old, new])
        self.assertNotIn(_key(old_road), segs)
        self.assertEqual(rep[1]["removed_stale"], 1)

    def test_parallel_road_twenty_metres_away_does_not_replace(self):
        old_road = road(0, 0)
        old = export("old", "20260901-10:00:00", [(old_road, "unvisited")])
        new = export("new", "20260902-10:00:00", [(road(0, 0, dy=20 * M), "unvisited")])
        segs, _, rep = merge_exports([old, new])
        self.assertIn(_key(old_road), segs)
        self.assertEqual((rep[1]["removed_stale"], rep[1]["kept_absent"]), (0, 1))

    def test_a_road_only_crossed_by_a_new_road_is_kept(self):
        old_road = road(0, 0)
        crossing = line(LON + D / 2, LAT - 0.001, LON + D / 2, LAT + 0.001, 4)
        old = export("old", "20260901-10:00:00", [(old_road, "unvisited")])
        new = export("new", "20260902-10:00:00", [(crossing, "unvisited")], bbox=(LON, LAT - 0.001, LON + D, LAT + 0.001))
        segs, _, rep = merge_exports([old, new])
        self.assertIn(_key(old_road), segs)

    def test_other_activity_never_removes(self):
        foot_road = road(0, 0)
        foot = export("foot", "20260901-10:00:00", [(foot_road, "unvisited")], activity="foot")
        bike = export("bike", "20260902-10:00:00", [(road(0, 0, dy=M), "unvisited")], activity="bike")
        segs, _, rep = merge_exports([foot, bike])
        self.assertIn(_key(foot_road), segs)
        self.assertEqual(len(segs), 2)
        self.assertEqual((rep[1]["removed_stale"], rep[1]["kept_absent"]), (0, 0))

    def test_same_activity_after_an_other_one_still_removes(self):
        """The activity checked is the one the old segment came from."""
        r1 = road(0, 0)
        bike1 = export("bike1", "20260901-10:00:00", [(r1, "unvisited")], activity="bike")
        foot = export("foot", "20260902-10:00:00", [(road(0, 0.01), "unvisited")], activity="foot")
        bike2 = export("bike2", "20260903-10:00:00", [(road(0, 0, dy=M), "unvisited")], activity="bike")
        segs, _, rep = merge_exports([bike1, foot, bike2])
        self.assertNotIn(_key(r1), segs)
        self.assertEqual(rep[2]["removed_stale"], 1)

    def test_unknown_activity_is_compatible(self):
        r = road(0, 0)
        old = export("old", "20260901-10:00:00", [(r, "unvisited")], activity="")
        new = export("new", "20260902-10:00:00", [(road(0, 0, dy=M), "unvisited")], activity="bike")
        segs, _, _ = merge_exports([old, new])
        self.assertNotIn(_key(r), segs)

    def test_export_without_traveled_roads_cannot_remove_a_visited_one(self):
        r = road(0, 0)
        old = export("old", "20260901-10:00:00", [(r, "visited")])
        new = export("new", "20260902-10:00:00", [(road(0, 0, dy=M), "unvisited")], traveled=False)
        segs, _, rep = merge_exports([old, new])
        self.assertIn(_key(r), segs)
        self.assertEqual(rep[1]["removed_stale"], 0)

    def test_export_without_untraveled_roads_cannot_remove_an_unvisited_one(self):
        r = road(0, 0)
        old = export("old", "20260901-10:00:00", [(r, "unvisited")])
        new = export("new", "20260902-10:00:00", [(road(0, 0, dy=M), "visited")], untraveled=False)
        segs, _, rep = merge_exports([old, new])
        self.assertIn(_key(r), segs)

    def test_road_outside_the_bbox_of_the_newer_export_is_untouched(self):
        far = road(0.5, 0)
        old = export("old", "20260901-10:00:00", [(far, "unvisited")])
        new = export("new", "20260902-10:00:00", [(road(0, 0), "unvisited")])
        segs, _, rep = merge_exports([old, new])
        self.assertIn(_key(far), segs)
        self.assertEqual((rep[1]["removed_stale"], rep[1]["kept_absent"]), (0, 0))

    def test_boundaries_are_merged_by_ring(self):
        ring = line(LON, LAT, LON + D, LAT) + line(LON + D, LAT + D, LON, LAT + D) + [(LON, LAT)]
        a = export("a", "20260901-10:00:00", [(road(0, 0), "unvisited")], boundaries=[ring])
        b = export("b", "20260902-10:00:00", [(road(0, 0), "unvisited")], boundaries=[ring])
        _, bnd, rep = merge_exports([a, b])
        self.assertEqual(len(bnd), 1)
        self.assertEqual(rep[1]["new_boundaries"], 0)

    def test_synthetic_kmz_exports(self):
        """Same files as the GUI tests: reading then merging the same export twice changes nothing."""
        with tempfile.TemporaryDirectory() as d:
            a, b = os.path.join(d, "a.kmz"), os.path.join(d, "b.kmz")
            synthetic.write_wandrer_kmz(a)
            synthetic.write_wandrer_kmz(b, stamp="20260927-08:00:00")
            segs, bnd, rep = merge_exports([read_export(a), read_export(b)])
        self.assertEqual(len(segs), 4)
        self.assertEqual(len(bnd), 1)
        self.assertEqual((rep[1]["added"], rep[1]["removed_stale"], rep[1]["kept_absent"]), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
