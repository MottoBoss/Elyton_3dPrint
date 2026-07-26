"""Self-check for toolpath.py. Run: python test_toolpath.py"""
import numpy as np

import gcodesim as gs  # used to read our own output back, to prove G91 can't drift
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

    # contour fill: same extents as raster, one ECM cycle for the whole region
    fills = tp.offset_fill_paths(m, sx, sy, 0.5, 0.05)
    fp = np.vstack(fills)
    assert 1.9 <= fp[:, 0].min() and fp[:, 0].max() <= 18.1
    assert 2.9 <= fp[:, 1].min() and fp[:, 1].max() <= 7.1
    assert len(fills) == 1, f"{len(fills)} cycles, expected 1 (raster used {len(segs)})"

    # hop test: along the bar stays in material, off the bar does not
    assert tp._inside(m, sx, sy, np.array([3.0, 5.0]), np.array([17.0, 5.0]))
    assert not tp._inside(m, sx, sy, np.array([3.0, 5.0]), np.array([3.0, 9.0]))

    # separate regions can never share a cycle: the hop would etch background
    two = m.copy()
    two[:, 90:110] = False
    assert len(tp.offset_fill_paths(two, sx, sy, 0.5, 0.05)) == 2

    # ink running off the image edge still offsets inward from that edge; any fill
    # of area A with spacing s is ~A/s long, so a blown border check shows up here
    edge = np.ones((100, 200), bool)
    total = sum(np.hypot(*np.diff(p, axis=0).T).sum()
                for p in tp.offset_fill_paths(edge, sx, sy, 0.5, 0.05))
    ideal = edge.sum() * sx * sy / 0.5
    assert total < 1.5 * ideal, f"{total:.0f} mm of path for a {ideal:.0f} mm fill"

    # radius compensation shrinks extents by ~0.5 mm per side (r = 5 px)
    segs2 = tp.raster_paths(tp.erode_disk(m, 5, 5), sx, sy, 0.5)
    p2 = np.vstack(segs2)
    assert p2[:, 0].min() >= 2.4 and p2[:, 0].max() <= 17.6

    # thin-feature detection: 3 px bar is invisible to a 10 px tool
    thin = np.zeros((100, 200), bool)
    thin[50:53, 20:180] = True
    assert tp.thin_fraction(thin, 5, 5) > 0.9
    assert tp.thin_fraction(m, 5, 5) < 0.2

    # feeds are mm/s in, mm/min out; the cut is gated by ecm_on/off, never by Z
    g, st = tp.emit_gcode(loops, cut_feed=1.0, travel_feed=25.0, passes=2,
                          relative=False, header_lines=["check"])
    assert "G90" in g and "G21" in g and "G91" not in g
    assert not [l for l in g.splitlines() if "Z" in l], "a Z move leaked in"
    assert "F60" in g and "F1500" in g, "mm/s was not converted to mm/min"
    assert g.count("ecm_on") == 2, "one ECM cycle per loop per pass"
    assert g.count("ecm_off") == 3, "one ecm_off per cycle plus the safety one"
    assert st["cut_mm"] > 0 and st["minutes"] > 0 and st["ecm_cycles"] == 2

    # ECM gates must strictly alternate in every mode: two ecm_on or two ecm_off
    # in a row damages the controller
    for mode_paths in (segs, fills, loops):
        for rel in (True, False):
            txt, _ = tp.emit_gcode(mode_paths, cut_feed=1.0, travel_feed=25.0,
                                   passes=3, relative=rel)
            gates = [l for l in txt.splitlines() if l.startswith("ecm_")]
            assert gates[0] == "ecm_off", "program must start with the ECM off"
            assert gates[-1] == "ecm_off", "program must not end with the ECM live"
            assert all(a != b for a, b in zip(gates, gates[1:])), \
                "back-to-back ECM gate emitted"

    # relative (G91) must reconstruct to exactly the absolute program's points --
    # the drift incident, in test form. Scaled by an ugly factor first so the
    # coordinates do NOT land on the 3-decimal grid: with round numbers nothing
    # rounds and any implementation looks correct.
    skew = [p * 1.2345 for p in fills]
    rel_g, _ = tp.emit_gcode(skew, cut_feed=1.0, travel_feed=25.0, passes=25,
                             relative=True)
    abs_g, _ = tp.emit_gcode(skew, cut_feed=1.0, travel_feed=25.0, passes=25,
                             relative=False)
    assert "G91" in rel_g and "G90" not in rel_g
    pr = [m.p1[:2] for m in gs.parse(rel_g)[0]]
    pa = [m.p1[:2] for m in gs.parse(abs_g)[0]]
    assert len(pr) == len(pa) > 400, (len(pr), len(pa))
    worst = max(abs(a - b) for u, v in zip(pr, pa) for a, b in zip(u, v))
    assert worst < 1e-9, f"relative output drifted {worst} mm from absolute"
    assert max(abs(c) for c in pr[-1]) < 1e-9, "program does not end at (0,0)"

    print("all checks pass")


if __name__ == "__main__":
    main()
