"""Self-check for toolpath.py. Run: python test_toolpath.py"""
import numpy as np

import toolpath as tp


def main():
    # 200x100 px mask, filled rect: cols 20..179, rows 30..69.
    # At 0.1 mm/px the rect is 16 x 4 mm spanning x 2..18, y 3..7.
    m = np.zeros((100, 200), bool)
    m[30:70, 20:180] = True
    sx = sy = 0.1

    segs = tp.raster_paths(m, sx, sy, 0.5)
    assert segs, "no raster segments"
    pts = np.vstack(segs)
    assert 1.9 <= pts[:, 0].min() and pts[:, 0].max() <= 18.1
    assert 2.9 <= pts[:, 1].min() and pts[:, 1].max() <= 7.1
    dirs = [np.sign(s[1, 0] - s[0, 0]) for s in segs]
    assert dirs[0] == -dirs[1], "not boustrophedon"

    loops = tp.trace_paths(m, sx, sy, 0.05)
    assert len(loops) == 1
    assert np.allclose(loops[0][0], loops[0][-1]), "loop not closed"

    # radius compensation shrinks extents by ~0.5 mm per side (r = 5 px)
    segs2 = tp.raster_paths(tp.erode_disk(m, 5, 5), sx, sy, 0.5)
    p2 = np.vstack(segs2)
    assert p2[:, 0].min() >= 2.4 and p2[:, 0].max() <= 17.6

    # thin-feature detection: 3 px bar is invisible to a 10 px tool
    thin = np.zeros((100, 200), bool)
    thin[50:53, 20:180] = True
    assert tp.thin_fraction(thin, 5, 5) > 0.9
    assert tp.thin_fraction(m, 5, 5) < 0.2

    g, st = tp.emit_gcode(loops, cut_feed=60, travel_feed=1600,
                          z_travel=2.0, z_cut=0.0, passes=2, header_lines=["check"])
    assert "G90" in g and "G21" in g and "G91" not in g
    assert g.count("G1 Z0.00") == 2, "one plunge per loop per pass"
    assert st["cut_mm"] > 0 and st["minutes"] > 0 and st["paths"] == 2

    g2, _ = tp.emit_gcode(loops, cut_feed=60, travel_feed=1600,
                          z_travel=2.0, z_cut=0.0, omit_z=True)
    assert "Z" not in g2, "omit_z leaked a Z move"

    print("all checks pass")


if __name__ == "__main__":
    main()
