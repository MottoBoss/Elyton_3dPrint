"""Pure toolpath logic for the Elyton ECM app: mask -> polylines -> G-code.

No Qt imports here so it stays testable from the command line.
Coordinates: origin (0,0) = bottom-left of the artwork box, Y up, millimeters.
sx/sy are mm-per-pixel scales (they differ only if aspect lock is off).
"""
import numpy as np
import cv2


def make_mask(rgba, mode="auto", threshold=128, invert=False):
    """rgba: HxWx4 uint8. Returns bool array, True = ink (material to etch)."""
    alpha = rgba[:, :, 3]
    if mode == "auto":
        mode = "alpha" if (alpha < 128).any() else "dark"
    if mode == "alpha":
        m = alpha >= threshold
    else:  # dark pixels, composited over white so transparent black doesn't count
        gray = cv2.cvtColor(rgba, cv2.COLOR_RGBA2GRAY).astype(np.float32)
        a = alpha.astype(np.float32) / 255.0
        m = (gray * a + 255.0 * (1.0 - a)) < threshold
    return ~m if invert else m


def _disk(rx, ry):
    kx = max(1, int(round(rx)) * 2 + 1)
    ky = max(1, int(round(ry)) * 2 + 1)
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kx, ky))


def erode_disk(mask, rx, ry):
    """Tool-radius compensation: tool center confined here keeps the tip inside ink."""
    if rx < 0.5 and ry < 0.5:
        return mask
    return cv2.erode(mask.astype(np.uint8), _disk(rx, ry)).astype(bool)


def thin_fraction(mask, rx, ry):
    """Fraction of ink area lost to a morphological opening by the tool disk,
    i.e. the share of the artwork too thin for the tool to resolve."""
    ink = int(mask.sum())
    if ink == 0 or (rx < 0.5 and ry < 0.5):
        return 0.0
    opened = cv2.dilate(erode_disk(mask, rx, ry).astype(np.uint8), _disk(rx, ry)).astype(bool)
    return 1.0 - int((mask & opened).sum()) / ink


def _mm_loops(binary, sx, sy, tol_mm):
    """Contours of a uint8 mask as open mm polygons (first point not repeated)."""
    H = binary.shape[0]
    cnts, _ = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    eps_px = tol_mm / ((sx + sy) / 2.0)
    out = []
    for c in cnts:
        pts = cv2.approxPolyDP(c, eps_px, True).reshape(-1, 2).astype(np.float64)
        if len(pts) < 2:
            continue  # single-pixel speck: nothing the tool can follow
        out.append(np.column_stack([(pts[:, 0] + 0.5) * sx,
                                    (H - pts[:, 1] - 0.5) * sy]))
    return out


def trace_paths(mask, sx, sy, tol_mm):
    """Closed centerline loops along mask boundaries (outer edges and holes)."""
    return [np.vstack([l, l[:1]])
            for l in _mm_loops(mask.astype(np.uint8), sx, sy, tol_mm) if len(l) >= 3]


def _inside(mask, sx, sy, a, b):
    """True if the straight segment a->b never leaves the mask, i.e. it can be
    etched with the ECM left on instead of toggling off for a rapid."""
    H, W = mask.shape
    n = int(2 * max(abs(b[0] - a[0]) / sx, abs(b[1] - a[1]) / sy)) + 1
    p = a + (b - a) * np.linspace(0.0, 1.0, n + 1)[:, None]
    cols = np.clip(np.rint(p[:, 0] / sx - 0.5).astype(int), 0, W - 1)
    rows = np.clip(np.rint(H - 0.5 - p[:, 1] / sy).astype(int), 0, H - 1)
    return bool(mask[rows, cols].all())


HOP_STEPOVERS = 2.0  # longest ECM-on hop, in stepovers: a hop re-etches whatever
                     # it crosses, so a long one leaves a double-depth scar. Raise
                     # it to trade scars for fewer ECM cycles.


def offset_fill_paths(mask, sx, sy, stepover_mm, tol_mm):
    """Contour-parallel fill: trace each region's outline, then concentric inward
    offsets a stepover apart until the region is consumed. Rings are walked
    nearest-first from the outermost one, so the path spirals in, and the hop
    between rings stays ECM-on while it is short and runs through material --
    a whole region is usually one ecm_on/ecm_off cycle, where raster_paths needs
    one per scanline."""
    scale = (sx + sy) / 2.0
    step = max(stepover_mm, 1e-3)
    n_lab, labels = cv2.connectedComponents(mask.astype(np.uint8))
    paths = []
    for lab in range(1, n_lab):
        # 1 px of zero border, or distanceTransform sees no edge at all where the
        # artwork runs off the image and the offsets march in from the wrong side
        comp = cv2.copyMakeBorder((labels == lab).astype(np.uint8), 1, 1, 1, 1,
                                  cv2.BORDER_CONSTANT, value=0)
        # mm from the region edge: level L contours = the outline offset in by L
        dist = cv2.distanceTransform(comp, cv2.DIST_L2, 5) * scale
        shift = np.array([sx, sy])  # undo the pad, back into artwork coordinates
        rings, lvl = [], 0.0
        while lvl <= float(dist.max()):
            loops = [l - shift for l in
                     _mm_loops((dist > lvl).astype(np.uint8), sx, sy, tol_mm)]
            if not loops:
                break  # deeper levels are subsets, so they are empty too
            rings.append(loops)
            lvl += step
        # outermost ring first, then always the nearest ring left: that spirals
        # inward one stepover at a time instead of hopping across the region and
        # re-etching what it crosses.
        # ponytail: O(loops^2) nearest search, fine at a few hundred rings
        loops = [l for ring in rings for l in ring]
        run, cur = [], None
        while loops:
            if cur is None:
                j, i = 0, 0
            else:
                j, (i, _) = min(((k, _nearest(l, cur)) for k, l in enumerate(loops)),
                                key=lambda t: t[1][1])
            loop = np.roll(loops.pop(j), -i, axis=0)
            loop = np.vstack([loop, loop[:1]])  # close it
            short = run and np.hypot(*(loop[0] - cur)) <= HOP_STEPOVERS * step
            if short and _inside(mask, sx, sy, cur, loop[0]):
                run.append(loop)                # hop stays inside: ECM stays on
            else:
                if run:
                    paths.append(np.vstack(run))
                run = [loop]
            cur = loop[-1]
        if run:
            paths.append(np.vstack(run))
    return paths


def _nearest(loop, pt):
    d = np.hypot(loop[:, 0] - pt[0], loop[:, 1] - pt[1])
    i = int(d.argmin())
    return i, float(d[i])


def raster_paths(mask, sx, sy, stepover_mm):
    """Boustrophedon scanline fill, bottom-up, alternating direction per scanline.
    Returns a list of 2x2 arrays [[x0,y],[x1,y]]. Single-pixel runs become
    zero-length segments -- an ecm_on/ecm_off in place, i.e. a dot.
    One ECM cycle per scanline run; offset_fill_paths trades that for one
    per region."""
    H, W = mask.shape
    out = []
    leftward = False
    last_row = -1
    y = 0.5 * sy
    top = H * sy
    while y < top:
        row = H - 1 - int(y / sy)
        if 0 <= row < H and row != last_row:
            last_row = row
            cols = np.flatnonzero(mask[row])
            if cols.size:
                breaks = np.flatnonzero(np.diff(cols) > 1)
                starts = np.concatenate(([0], breaks + 1))
                ends = np.concatenate((breaks, [cols.size - 1]))
                runs = [((cols[s] + 0.5) * sx, (cols[e] + 0.5) * sx)
                        for s, e in zip(starts, ends)]
                if leftward:
                    runs = [(b, a) for a, b in reversed(runs)]
                ym = (H - row - 0.5) * sy  # snap to the pixel row actually sampled
                for x0, x1 in runs:
                    out.append(np.array([[x0, ym], [x1, ym]]))
                leftward = not leftward
        y += stepover_mm
    return out


ECM_ON, ECM_OFF = "ecm_on", "ecm_off"


def emit_gcode(paths, *, cut_feed, travel_feed, passes=1, relative=True,
               header_lines=()):
    """paths: list of Nx2 mm arrays, each one continuous with the ECM on.
    cut_feed/travel_feed are mm/SECOND; G-code F words are mm/min, so they go
    out multiplied by 60. Returns (gcode_text, stats dict).

    The cut is gated by ecm_off/ecm_on, never by Z -- no Z word is emitted, the
    electrode stays put. Every gate goes through gate(), which drops a toggle
    that repeats the current state, so ecm_on/ecm_on and ecm_off/ecm_off can
    never come out back to back -- the controller does not survive that.

    The program starts and ends at (0,0) with the ECM off, so it re-runs cleanly.

    relative=True emits G91 deltas. Each delta is measured from the position the
    MACHINE believes it is at -- the running sum of the deltas already sent, at
    the same 3 decimals -- not from the ideal path. So the rounding self-corrects
    on the next move instead of piling up: error stays under half a micron no
    matter how long the program is. That is what went wrong in the G91 incident."""
    g = [f"; {ln}" for ln in header_lines]
    g += ["G21", "G91" if relative else "G90"]
    fc, ft = cut_feed * 60.0, travel_feed * 60.0
    cut_d = travel_d = 0.0
    cmd = (0.0, 0.0)   # where the machine thinks it is, to the emitted decimal
    live = None        # current ECM gate state

    def gate(on):
        nonlocal live
        if on is not live:
            g.append(ECM_ON if on else ECM_OFF)
            live = on

    def move(code, x, y, feed):
        nonlocal cmd
        if relative:
            dx, dy = round(x - cmd[0], 3), round(y - cmd[1], 3)
            g.append(f"{code} X{dx:.3f} Y{dy:.3f} F{feed:g}")
            cmd = (cmd[0] + dx, cmd[1] + dy)
        else:
            g.append(f"{code} X{x:.3f} Y{y:.3f} F{feed:g}")
            cmd = (round(x, 3), round(y, 3))

    gate(False)
    for _ in range(max(1, passes)):
        for path in paths:
            x0, y0 = path[0]
            travel_d += float(np.hypot(x0 - cmd[0], y0 - cmd[1]))
            move("G0", x0, y0, ft)
            gate(True)
            for x, y in path[1:]:
                move("G1", x, y, fc)
            seg = np.diff(path, axis=0)
            cut_d += float(np.hypot(seg[:, 0], seg[:, 1]).sum())
            gate(False)
    if cmd != (0.0, 0.0):  # close the program out where it started
        travel_d += float(np.hypot(*cmd))
        move("G0", 0.0, 0.0, ft)
        g[-1] += "  ; back to start"
    cycles = len(paths) * max(1, passes)
    minutes = (cut_d / cut_feed + travel_d / travel_feed) / 60.0
    stats = {"cut_mm": cut_d, "travel_mm": travel_d, "minutes": minutes,
             "paths": cycles, "ecm_cycles": cycles}
    return "\n".join(g) + "\n", stats
