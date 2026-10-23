import copy
import os
import re
import unittest
import xml.dom.minidom

from wahoondrer import theme_style

THEME = os.path.join(os.path.dirname(__file__), "..", "themes", "vtm", "vtm-elemnt.xml")


def read():
    with open(THEME, encoding="utf-8") as f:
        return f.read()


def line(text, id_):
    return re.search(rf'<style-line id="{id_}"[^>]*>', text).group(0)


class ThemeStyleTests(unittest.TestCase):
    def test_default_leaves_the_theme_untouched(self):
        self.assertEqual(theme_style.apply(read(), theme_style.DEFAULT), read())

    def test_default_values_are_those_of_the_theme(self):
        text = read()
        for mode, cats in (("light", ("roam", "bolt", "bolt2")), ("dark", ("bolt2-dark",))):
            for cat in cats:
                for kind, key in (("unvisited", "unvisited"), ("unpaved", "unvisited"), ("visited", "visited")):
                    d = theme_style.DEFAULT[mode][key]
                    l = line(text, f"wandrer-{kind}-{cat}")
                    self.assertIn(f'stroke="{d["color"]}"', l)
                    self.assertIn(f'width="{d["width"]:g}"' if d["width"] != 1.0 else 'width="1.0"', l)

    def test_unvisited_colour_width_and_outline_per_mode(self):
        look = theme_style.defaults()
        look["light"]["unvisited"] = {"color": "#00aaff", "width": 2, "outline": True}
        out = theme_style.apply(read(), look)
        for cat in ("roam", "bolt", "bolt2"):
            for kind in ("unvisited", "unpaved"):
                self.assertIn('stroke="#00AAFF" width="2"', line(out, f"wandrer-{kind}-{cat}"))
                self.assertIn('width="2.9"', line(out, f"wandrer-{kind}-outline-{cat}"))
        self.assertEqual(line(out, "wandrer-unvisited-bolt2-dark"), line(read(), "wandrer-unvisited-bolt2-dark"))

    def test_no_unvisited_outline_only_in_the_chosen_mode(self):
        look = theme_style.defaults()
        look["dark"]["unvisited"]["outline"] = False
        out = theme_style.apply(read(), look)
        self.assertNotIn('use="wandrer-unvisited-outline-bolt2-dark"', out)
        self.assertNotIn('use="wandrer-unpaved-outline-bolt2-dark"', out)
        self.assertIn('use="wandrer-unvisited-outline-bolt2"', out)
        xml.dom.minidom.parseString(out.encode("utf-8"))

    def test_visited_look_and_outline(self):
        look = theme_style.defaults()
        look["light"]["visited"] = {"color": "#112233", "width": 1.2, "outline": True}
        out = theme_style.apply(read(), look)
        self.assertIn('stroke="#112233" width="1.2"', line(out, "wandrer-visited-bolt"))
        self.assertIn('width="2.1"', line(out, "wandrer-visited-outline-bolt"))
        self.assertRegex(out, r'<line use="wandrer-visited-outline-roam" />\s*<line use="wandrer-visited-roam" />')
        self.assertNotIn('use="wandrer-visited-outline-bolt2-dark"', out)       # the dark one keeps its default: no outline
        xml.dom.minidom.parseString(out.encode("utf-8"))

    def test_boundaries_are_not_touched(self):
        look = theme_style.defaults()
        look["light"]["unvisited"]["color"] = "#112233"
        out = theme_style.apply(read(), look)
        for name in ("wandrer-boundary-bolt2", "wandrer-boundary-halo-roam", "wandrer-boundary-bolt2-dark"):
            self.assertEqual(line(out, name), line(read(), name))

    def test_clean_completes_and_checks(self):
        self.assertEqual(theme_style.clean(None), theme_style.clean(theme_style.DEFAULT))
        self.assertEqual(theme_style.clean({"dark": {"visited": {"color": "#abcdef"}}})["dark"]["visited"]["color"], "#ABCDEF")
        for bad in ({"light": {"unvisited": {"color": "orange"}}}, {"dark": {"visited": {"width": 0}}},
                    {"light": {"visited": {"width": 11}}}, {"light": {"visited": {"width": "x"}}}):
            with self.assertRaises(ValueError):
                theme_style.clean(bad)

    def test_a_changed_theme_is_detected(self):
        with self.assertRaises(ValueError):
            theme_style.apply(read().replace("wandrer-unpaved-roam", "wandrer-other-roam"), theme_style.DEFAULT)

    def test_every_line_used_by_the_theme_is_defined(self):
        text = read()
        ids = set(re.findall(r'<style-line id="([^"]+)"', text))
        self.assertFalse(set(re.findall(r'<line use="(wandrer-[^"]+)"', text)) - ids)
        out = theme_style.apply(text, {"light": {"visited": {"outline": True}}, "dark": {"visited": {"outline": True}}})
        self.assertFalse(set(re.findall(r'<line use="(wandrer-[^"]+)"', out)) - ids)

    def test_defaults_are_a_copy(self):
        d = theme_style.defaults(); d["light"]["visited"]["width"] = 9
        self.assertEqual(theme_style.DEFAULT["light"]["visited"]["width"], 1.0)


class SquadratsLookTest(unittest.TestCase):
    def sq_lines(self, text):
        return {m.group(1): m.group(0) for m in re.finditer(r'<style-line id="(squadrat-[^"]+)"[^>]*/>', text)}

    def test_default_look_leaves_the_theme_unchanged(self):
        self.assertEqual(theme_style.sq_apply(read(), theme_style.SQ_DEFAULT), read())
        self.assertTrue(theme_style.sq_is_default({}))

    def test_colour_width_and_dashes(self):
        out = self.sq_lines(theme_style.sq_apply(read(), {
            "light": {"big": {"color": "#0000ff", "dashed": True}, "small": {"width": 2, "dashed": False}}}))
        self.assertIn('stroke="#0000FF"', out["squadrat-big-bolt"])
        self.assertIn('dasharray="6,6"', out["squadrat-big-bolt"])
        self.assertNotIn("dasharray", out["squadrat-small-roam"])
        self.assertIn('width="2"', out["squadrat-small-roam"])
        self.assertIn('stroke="#FF6060"', out["squadrat-big-bolt2-dark"])          # dark style untouched
        self.assertIn('dasharray="6,6"', out["squadrat-small-bolt2-dark"])

    def test_bad_values_and_changed_theme(self):
        for bad in ({"light": {"big": {"color": "blue"}}}, {"dark": {"small": {"width": 0}}}):
            with self.assertRaises(ValueError):
                theme_style.sq_clean(bad)
        with self.assertRaises(ValueError):
            theme_style.sq_apply(read().replace("squadrat-big-roam", "squadrat-other-roam"), theme_style.SQ_DEFAULT)


if __name__ == "__main__":
    unittest.main()
