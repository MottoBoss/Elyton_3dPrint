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


def trace_paths(mask, sx, sy, tol_mm):
    """Closed centerline loops along mask boundaries (outer edges and holes)."""
    H = mask.shape[0]
    cnts, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    eps_px = tol_mm / ((sx + sy) / 2.0)
    out = []
    for c in cnts:
        c = cv2.approxPolyDP(c, eps_px, True)
        if len(c) < 3:
            continue
        pts = c.reshape(-1, 2).astype(np.float64)
        xs = (pts[:, 0] + 0.5) * sx
        ys = (H - pts[:, 1] - 0.5) * sy
        loop = np.column_stack([xs, ys])
        out.append(np.vstack([loop, loop[:1]]))  # close the loop
    return out


def raster_paths(mask, sx, sy, stepover_mm):
    """Boustrophedon scanline fill, bottom-up, alternating direction per scanline.
    Returns a list of 2x2 arrays [[x0,y],[x1,y]]. Single-pixel runs become
    zero-length segments (plunge/lift dwell = a dot)."""
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


def emit_gcode(paths, *, cut_feed, travel_feed, z_travel, z_cut,
               passes=1, omit_z=False, header_lines=()):
    """paths: list of Nx2 mm arrays. Returns (gcode_text, stats dict).
    Always G90/G21 absolute millimeters -- never relative (see the drift incident)."""
    g = [f"; {ln}" for ln in header_lines]
    g += ["G21", "G90"]
    if not omit_z:
        g.append(f"G0 Z{z_travel:.2f} F{travel_feed:g}")
    cut_d = travel_d = z_d = 0.0
    pos = None
    for _ in range(max(1, passes)):
        for path in paths:
            x0, y0 = path[0]
            g.append(f"G0 X{x0:.3f} Y{y0:.3f} F{travel_feed:g}")
            if pos is not None:
                travel_d += float(np.hypot(x0 - pos[0], y0 - pos[1]))
            if not omit_z:
                g.append(f"G1 Z{z_cut:.2f} F{travel_feed:g}")
                z_d += 2.0 * abs(z_travel - z_cut)
            for x, y in path[1:]:
                g.append(f"G1 X{x:.3f} Y{y:.3f} F{cut_feed:g}")
            seg = np.diff(path, axis=0)
            cut_d += float(np.hypot(seg[:, 0], seg[:, 1]).sum())
            if not omit_z:
                g.append(f"G1 Z{z_travel:.2f} F{travel_feed:g}")
            pos = path[-1]
    minutes = cut_d / cut_feed + (travel_d + z_d) / travel_feed  # feeds are mm/min
    stats = {"cut_mm": cut_d, "travel_mm": travel_d, "z_mm": z_d,
             "minutes": minutes, "paths": len(paths) * max(1, passes)}
    return "\n".join(g) + "\n", stats
