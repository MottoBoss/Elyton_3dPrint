"""Self-check for gcodesim.py. Run: python test_gcodesim.py"""
import gcodesim as gs

SQUARE_ABS = """
G21
G90
G0 Z2 F1600
G0 X10 Y10 F1600
G1 Z0 F1600
G1 X20 Y10 F60
G1 X20 Y20 F60
G1 X10 Y20 F60
G1 X10 Y10 F60
G1 Z2 F1600
"""

# relative square whose X leg is 0.5 mm short -> loop doesn't close
SQUARE_REL_BAD = """
G21
G91
G1 Z-2 F1600
G1 X10 F60
G1 Y10 F60
G1 X-9.5 F60
G1 Y-10 F60
G1 Z2 F1600
G1 Z-2 F1600
G1 X10 F60
G1 Y10 F60
G1 X-9.5 F60
G1 Y-10 F60
G1 Z2 F1600
"""


def main():
    moves, flags, unsup = gs.parse(SQUARE_ABS)
    st, warns = gs.analyze(moves, flags, unsup, z_band=(-0.5, 2.5))
    assert st["cut_mm"] == 40.0, st["cut_mm"]
    assert st["size"] == (20.0, 20.0)  # includes rapid from origin
    assert st["mode"].startswith("G90")
    assert not any("Relative" in w for w in warns)

    moves, flags, unsup = gs.parse(SQUARE_REL_BAD)
    st, warns = gs.analyze(moves, flags, unsup)
    assert flags["g91"]
    bad, total, drift = gs.loop_drift(moves)
    assert (bad, total) == (2, 2), (bad, total)
    assert abs(drift - 1.0) < 1e-6, drift  # two 0.5 mm errors accumulate
    assert any("Relative" in w for w in warns)
    assert any("do not close" in w for w in warns)

    # closed relative loop -> no drift
    good = SQUARE_REL_BAD.replace("X-9.5", "X-10")
    bad, total, drift = gs.loop_drift(gs.parse(good)[0])
    assert bad == 0 and drift == 0.0

    # inches: 1 inch square = 25.4 mm
    moves, flags, _ = gs.parse("G20\nG90\nG0 X0 Y0\nG1 X1 Y0 F60")
    assert abs(moves[-1].p1[0] - 25.4) < 1e-9

    # G92 offsets, G91 G1 on one line, bed/Z warnings
    moves, flags, unsup = gs.parse("G92 X100\nG91 G1 X5 F60\nG1 Z-30 F60")
    assert moves[0].p0[0] == 100 and moves[0].p1[0] == 105
    st, warns = gs.analyze(moves, flags, unsup, bed=(4, 4), z_band=(-0.5, 2.5))
    assert any("exceeds bed" in w for w in warns)
    assert any("Z range" in w for w in warns)

    print("all checks pass")


if __name__ == "__main__":
    main()
