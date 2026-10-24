"""Write Wandrer segments/boundaries as OSM XML (to be converted to .pbf).

Tag scheme: wandrer=visited|unvisited [+ surface=unpaved] for the roads,
wandrer=boundary for boundaries; squadrat=big|small for the outlines of
unexplored Squadrats squares. No highway tag: an unknown highway value
(highway=wandrer) left the map blank on an ELEMNT ROAM v1, and the theme
matches on wandrer=* anyway.

IDs: real OSM node IDs exceed 14 billion (2026), so Wandrer nodes start at 50e9
(also above WMC elevation nodes at 20e9 and sea/land at ~32.95e9). Ways start
at 9e9.
"""

NODE_ID_START = 50_000_000_000
WAY_ID_START = 9_000_000_000


def write_osm(path, segments, boundaries, with_traveled=False, squadrats=None):
    """Write the OSM XML file and return statistics.

    squadrats: optional {"big": [polyline, ...], "small": [...]} written as
    ways tagged squadrat=big|small (no highway tag).
    """
    node_id = NODE_ID_START
    way_id = WAY_ID_START
    stats = dict(ways=0, nodes=0, skipped_visited=0, squadrats={})

    with open(path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<osm version="0.6" generator="wahoondrer">\n')
        ways_xml = []

        def add_way(coords, tags):
            nonlocal node_id, way_id
            refs = []
            for lon, lat in coords:
                f.write(f'  <node id="{node_id}" lat="{lat:.7f}" lon="{lon:.7f}" />\n')
                refs.append(node_id)
                node_id += 1
            lines = [f'  <way id="{way_id}">\n']
            lines += [f'    <nd ref="{r}" />\n' for r in refs]
            lines += [f'    <tag k="{k}" v="{v}" />\n' for k, v in tags.items()]
            lines.append('  </way>\n')
            ways_xml.append("".join(lines))
            way_id += 1
            stats["ways"] += 1

        for seg in segments.values():
            if seg.state == "visited" and not with_traveled:
                stats["skipped_visited"] += 1
                continue
            tags = {"wandrer": seg.state}
            if seg.unpaved:
                tags["surface"] = "unpaved"
            add_way(seg.coords, tags)

        # A closed ring would become a closed way that the map writer may treat
        # as a filled area: split it into two open ways instead.
        for ring in boundaries.values():
            mid = len(ring) // 2
            add_way(ring[: mid + 1], {"wandrer": "boundary"})
            add_way(ring[mid:], {"wandrer": "boundary"})

        for level, lines in (squadrats or {}).items():
            for line in lines:
                add_way(line, {"squadrat": level})
            stats["squadrats"][level] = len(lines)

        stats["nodes"] = node_id - NODE_ID_START
        f.write("".join(ways_xml))
        f.write("</osm>\n")
    return stats
