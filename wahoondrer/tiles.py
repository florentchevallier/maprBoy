"""Zoom-8 slippy tiles, as used by WahooMapsCreator (WMC) and Wahoo devices."""

import math

ZOOM = 8


def tile_of(lon, lat, zoom=ZOOM):
    n = 2 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_r = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n)
    return x, y


def tile_bbox(x, y, zoom=ZOOM):
    """Return (left, bottom, right, top) in degrees."""
    n = 2 ** zoom

    def lat(yy):
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * yy / n))))

    return (x / n * 360.0 - 180.0, lat(y + 1), (x + 1) / n * 360.0 - 180.0, lat(y))


def tiles_for_bbox(west, south, east, north, zoom=ZOOM):
    """Return the (x, y) tiles intersecting the W, S, E, N bounding box."""
    x0, y1 = tile_of(west, south, zoom)
    x1, y0 = tile_of(east, north, zoom)
    return [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]
