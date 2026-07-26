"""G-code parsing and sanity analysis for the simulator. No Qt imports.

Parses G0/G1 (XYZF), G90/G91, G20/G21, G92, G28, ';' and '()' comments.
Everything is reconstructed into absolute millimeters, so the renderer and
stats never care what mode the file was written in.
"""
import math
import re
from dataclasses import dataclass

WORD = re.compile(r"([A-Za-z])\s*(-?\d+\.?\d*)")


@dataclass
class Move:
    rapid: bool          # G0 (or G28) vs G1
    p0: tuple            # (x, y, z) absolute mm
    p1: tuple
    feed: float          # mm/min
    line: int            # 1-based source line


def parse(text):
    """Returns (moves, flags, unsupported). flags: g90/g91/g20/g21 seen."""
    pos = [0.0, 0.0, 0.0]
    absolute = True      # Marlin/Klipper default
    scale = 1.0          # 25.4 in G20 mode
    feed = 1600.0        # fallback if the file never sets F
    flags = {"g90": False, "g91": False, "g20": False, "g21": False,
             "ecm_on": 0, "ecm_off": 0, "ecm_live": False, "ecm_repeat": [],
             "elyton": False}
    moves = []
    unsupported = {}
    for line_no, raw in enumerate(text.splitlines(), 1):
        if raw.lstrip().startswith("; Elyton ECM"):
            flags["elyton"] = True  # our own generator's header
        line = re.sub(r"\([^)]*\)", "", raw.split(";", 1)[0])
        low = line.strip().lower()
        if low in ("ecm_on", "ecm_off"):  # our cut gate; Z stays put
            on = low == "ecm_on"
            if flags["ecm_on"] + flags["ecm_off"] and on is flags["ecm_live"]:
                flags["ecm_repeat"].append(line_no)
            flags[low] += 1
            flags["ecm_live"] = on
            continue
        words = WORD.findall(line)
        if not words:
            continue
        gcodes, mcodes, args = [], [], {}
        for L, val in words:
            L = L.upper()
            if L == "G":
                gcodes.append(int(float(val)))
            elif L == "M":
                mcodes.append(int(float(val)))
            elif L in "XYZEF":
                args[L] = float(val)
        # modal state first, so "G91 G1 X5" works
        if 90 in gcodes: absolute = True;  flags["g90"] = True
        if 91 in gcodes: absolute = False; flags["g91"] = True
        if 21 in gcodes: scale = 1.0;      flags["g21"] = True
        if 20 in gcodes: scale = 25.4;     flags["g20"] = True
        if "F" in args:
            feed = args["F"] * scale
        if 92 in gcodes:  # redefine position, no motion
            for i, ax in enumerate("XYZ"):
                if ax in args:
                    pos[i] = args[ax] * scale
        elif 28 in gcodes:  # home: rapid to 0 on named axes (all if bare G28)
            new = list(pos)
            named = [ax for ax in "XYZ" if ax in args]
            for i, ax in enumerate("XYZ"):
                if not named or ax in named:
                    new[i] = 0.0
            moves.append(Move(True, tuple(pos), tuple(new), feed, line_no))
            pos = new
        elif 0 in gcodes or 1 in gcodes:
            new = list(pos)
            for i, ax in enumerate("XYZ"):
                if ax in args:
                    d = args[ax] * scale
                    new[i] = d if absolute else pos[i] + d
            moves.append(Move(0 in gcodes, tuple(pos), tuple(new), feed, line_no))
            pos = new
        else:
            for n in gcodes:  # M-codes are expected, ignore silently
                if n not in (90, 91, 20, 21):  # modals already handled above
                    unsupported[f"G{n}"] = unsupported.get(f"G{n}", 0) + 1
    return moves, flags, unsupported


def loop_drift(moves, tol=0.05):
    """For plunge..lift groups (Z down -> XY cuts -> Z up), a well-formed
    program returns to the plunge XY before lifting. Returns
    (open_loop_count, total_loops, net_drift_mm) -- the accumulated net
    offset is what wrecked us in the G91 incident."""
    open_loops, total = 0, 0
    net = [0.0, 0.0]
    start = None
    for m in moves:
        dz = m.p1[2] - m.p0[2]
        if dz < 0:
            start = (m.p1[0], m.p1[1])
        elif dz > 0 and start is not None:
            total += 1
            ex, ey = m.p0[0] - start[0], m.p0[1] - start[1]
            if math.hypot(ex, ey) > tol:
                open_loops += 1
                net[0] += ex
                net[1] += ey
            start = None
    return open_loops, total, math.hypot(*net)


def analyze(moves, flags, unsupported, *, bed=(220.0, 220.0), z_band=None):
    """Stats dict + list of warning strings. z_band=(lo, hi) expected Z range."""
    st = {"cut_mm": 0.0, "travel_mm": 0.0, "z_mm": 0.0, "minutes": 0.0,
          "cuts": 0, "rapids": 0, "extents": None, "ecm_cycles": flags["ecm_on"],
          "mode": ("G91 RELATIVE" if flags["g91"] else "G90 absolute")
                  + (", inches (G20)" if flags["g20"] else ", mm")}
    warnings = []
    if not moves:
        return st, ["No motion commands found."]
    xs, ys, zs = [], [], []
    for m in moves:
        dx, dy, dz = (m.p1[i] - m.p0[i] for i in range(3))
        xy = math.hypot(dx, dy)
        d3 = math.hypot(xy, dz)
        st["z_mm"] += abs(dz)
        if m.rapid:
            st["rapids"] += 1
            st["travel_mm"] += xy
        else:
            if xy > 0:
                st["cuts"] += 1
                st["cut_mm"] += xy
        st["minutes"] += d3 / max(m.feed, 1e-6)
        xs += [m.p0[0], m.p1[0]]
        ys += [m.p0[1], m.p1[1]]
        zs += [m.p0[2], m.p1[2]]
    ext = (min(xs), max(xs), min(ys), max(ys))
    st["extents"] = ext
    w, h = ext[1] - ext[0], ext[3] - ext[2]
    st["size"] = (w, h)
    st["z_range"] = (min(zs), max(zs))

    if flags["g91"] and not flags["elyton"]:
        # our own generator computes deltas from the commanded position and closes
        # the program out at the origin, so G91 in our files is not news
        warnings.append("Relative mode (G91) detected - we were burned by this "
                        "before. Verify the loops below.")
        bad, total, drift = loop_drift(moves)
        if bad:
            warnings.append(f"{bad} of {total} cut loops do not close; "
                            f"accumulated drift {drift:.2f} mm.")
    if flags["ecm_live"]:
        warnings.append("Program ends with the ECM ON - no ecm_off after the "
                        "last cut. Do not run this.")
    if flags["ecm_repeat"]:
        at = ", ".join(str(n) for n in flags["ecm_repeat"][:5])
        warnings.append(f"{len(flags['ecm_repeat'])} back-to-back ECM toggle(s) "
                        f"(same state twice) at line {at} - the controller cannot "
                        f"take that. Do not run this.")
    if not flags["g21"] and not flags["g20"]:
        warnings.append("No units command (G20/G21) - assuming millimeters.")
    if w > bed[0] or h > bed[1]:
        warnings.append(f"Toolpath {w:.1f} x {h:.1f} mm exceeds bed travel "
                        f"{bed[0]:g} x {bed[1]:g} mm.")
    if z_band:
        lo, hi = min(zs), max(zs)
        if lo < z_band[0] or hi > z_band[1]:
            warnings.append(f"Z range {lo:.2f}..{hi:.2f} outside expected band "
                            f"{z_band[0]:g}..{z_band[1]:g} mm.")
    if unsupported:
        names = ", ".join(f"{k} x{v}" for k, v in sorted(unsupported.items()))
        warnings.append(f"Unsupported commands skipped (not rendered): {names}")
    return st, warnings
