"""Tiny synthetic Wandrer / Squadrats files for tests (no personal data)."""
import math
import os
import zipfile

KML_NS = "http://www.opengis.net/kml/2.2"


def _line(style, pts):
    c = " ".join(f"{x:.6f},{y:.6f},0" for x, y in pts)
    return (f"<Placemark><styleUrl>#{style}</styleUrl><LineString><coordinates>{c}"
            f"</coordinates></LineString></Placemark>")


def write_wandrer_kmz(path, stamp="20260926-12:00:00", activity="bike", lon=11.50, lat=48.15):
    """Two traveled roads, two unvisited (one unpaved), one boundary ring."""
    d = 0.002
    body = [
        _line("Traveled", [(lon, lat), (lon + d, lat)]),
        _line("Traveled", [(lon + d, lat), (lon + 2 * d, lat)]),
        _line("Untraveled", [(lon, lat + d), (lon + d, lat + d)]),
        _line("UntraveledUnpaved", [(lon + d, lat + d), (lon + 2 * d, lat + d)]),
    ]
    ring = [(lon - d, lat - d), (lon + 3 * d, lat - d), (lon + 3 * d, lat + 3 * d),
            (lon - d, lat + 3 * d), (lon - d, lat - d)]
    c = " ".join(f"{x:.6f},{y:.6f},0" for x, y in ring)
    body.append(f"<Placemark><Polygon><outerBoundaryIs><LinearRing><coordinates>{c}"
                f"</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>")
    kml = (f'<?xml version="1.0" encoding="UTF-8"?><kml xmlns="{KML_NS}"><Document>'
           f"<name>Wandrer Export {stamp}</name>{''.join(body)}</Document></kml>")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(f"wandrer-{activity}-{stamp[2:8]}.kml", kml)


def _tx(lon, z):
    return (lon + 180.0) / 360.0 * 2 ** z


def _ty(lat, z):
    a = math.radians(lat)
    return (1 - math.log(math.tan(a) + 1 / math.cos(a)) / math.pi) / 2 * 2 ** z


def _lon(x, z):
    return x / 2 ** z * 360.0 - 180.0


def _lat(y, z):
    return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / 2 ** z))))


def write_squadrats_kml(path, lon=11.50, lat=48.15, size=2):
    """One explored block of size x size big squares (z14) and size*8 small ones (z17)."""
    def block(z, n):
        x0, y0 = int(_tx(lon, z)), int(_ty(lat, z))
        ring = [(x0, y0), (x0 + n, y0), (x0 + n, y0 + n), (x0, y0 + n), (x0, y0)]
        c = " ".join(f"{_lon(x, z):.7f},{_lat(y, z):.7f},0" for x, y in ring)
        return (f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{c}"
                f"</coordinates></LinearRing></outerBoundaryIs></Polygon>")
    kml = (f'<?xml version="1.0" encoding="UTF-8"?><kml xmlns="{KML_NS}"><Document>'
           f"<Placemark><name>squadrats</name>{block(14, size)}</Placemark>"
           f"<Placemark><name>squadratinhos</name>{block(17, size * 8)}</Placemark>"
           f"</Document></kml>")
    with open(path, "w", encoding="utf-8") as f:
        f.write(kml)
