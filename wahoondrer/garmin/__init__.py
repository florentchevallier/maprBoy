"""Garmin maps: a transparent overlay (.img) built with mkgmap from the Wandrer data.

look.py   how the roads are drawn (colour, width, white edge), day and night
style.py  mkgmap style: which OSM tag becomes which Garmin line type
typ.py    the TYP text file: how each Garmin type is drawn
build.py  KMZ exports -> .osm -> mkgmap -> gmapsupp.img, with checks
See docs/GARMIN.md for what was learned about mkgmap and the devices.
"""
