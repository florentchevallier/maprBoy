"""Hammerhead Karoo maps: one extra mapsforge file next to the Karoo's own maps, and our rules added to its theme.

Seen on a borrowed Karoo 2 (October 2026), see docs/DEVICES.md:
- the Karoo's maps are mapsforge binary files, one per region, in /sdcard/offline/maps/; an extra file put there
  (maprboy-<place>.map) is drawn over them, the Karoo's map stays visible, and it stayed after a sync;
- the theme is a VTM render theme, /sdcard/offline_vNN.xml (v15 on that device); rules added at its end are used;
- icons referenced as file:/<folder>/<name>.svg are read from /sdcard/<folder>/.
"""
