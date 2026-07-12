# Elyton ECM — User Guide

Desktop app for our ECM-converted Ender 3. The electrode never touches the metal:
depth comes from **dwell time and current**, not commanded Z. Feed rate is the depth
knob (slower feed = deeper etch). Z is only used two ways: lift +2 mm to travel,
drop to Z0 to cut.

## Install & run

```
cd Printer
python -m venv .venv                      (once)
.venv\Scripts\pip install -r requirements.txt   (once)
.venv\Scripts\python app.py
```

Self-checks (run any time, no hardware needed):

```
.venv\Scripts\python test_toolpath.py
.venv\Scripts\python test_gcodesim.py
```

---

## Tab 1: G-code Generator

Turns an image into ECM G-code. Pipeline: image → ink mask → toolpath → G-code.

### Workflow

1. **Open Image…** — PNG, JPG, BMP, GIF, or SVG. SVG is rasterized at 2000 px.
2. Check the **Ink mask** tab on the right: dark = what will be etched.
3. Set the output size, pick Raster or Trace, watch the **Toolpath** preview.
4. Fix any orange warning, then **Export G-code…**.

### Ink selection

| Control | What it does |
|---|---|
| **Ink is** | *Auto detect*: uses the alpha channel if the image has transparency, otherwise dark pixels. *Dark pixels*: ink = pixels darker than the threshold (transparent areas count as white). *Opaque pixels*: ink = pixels with alpha above the threshold. |
| **Threshold** | 1–254 slider. Cutoff for "dark" or "opaque". Watch the Ink mask tab while dragging. |
| **Invert** | Flips the mask — use for white-on-black artwork. |

### Output size

| Control | What it does |
|---|---|
| **Width / Height** | Physical size of the artwork box in mm. |
| **Lock aspect ratio** | On by default: edit one dimension, the other follows the image's pixel aspect. Uncheck only if you deliberately want distortion. |

### Tool & path

| Control | What it does |
|---|---|
| **Tool diameter** | Electrode tip diameter (default 1.27 mm). We swap tips — always set this. |
| **Mode** | *Raster fill*: boustrophedon scanlines (back-and-forth, alternating direction) filling every ink region — the everyday choice for solid etching. *Trace outline*: follows the boundaries of ink regions (outer edges **and** holes) as closed loops. |
| **Stepover** | Raster only. Scanline spacing as % of tool diameter. Default 50% = adjacent lines overlap half the tool for solid coverage. |
| **Simplify tol.** | Trace only. Polygon simplification tolerance in mm; higher = fewer points, coarser curves. |
| **Compensate tool radius** | **On (default)**: toolpath is inset by half the tool diameter so the *etched* result matches the artwork dimensions. Features narrower than the tool disappear (the warning below tells you how much). **Off**: the tool center rides the ink boundary — every feature grows ~0.64 mm per side with the 1.27 tip. |

### Feeds & Z

| Control | What it does |
|---|---|
| **Cut feed** | F for cutting moves (default 60 mm/min). This is the depth knob: slower = more dwell = deeper. |
| **Travel feed** | F for rapids, plunges, lifts (default 1600). |
| **Z travel / Z cut** | Lift height (2 mm) and cutting height (0). |
| **Passes** | Repeats the whole program N times for more dwell/depth. |
| **Omit Z moves** | Emits pure XY G-code — for when the future Z-gap PCB owns the Z axis. |

### Preview

- Blue = cut moves drawn at **true tool width** — overlapping raster lines merge into
  solid regions, so what you see is what gets etched.
- Dashed gray = travels. Red/green = X/Y axes at origin. Grid: 10 mm (1 mm when zoomed in).
- Mouse wheel zooms, drag pans.

### Warnings

The orange text appears when a meaningful share (>5%) of the ink is **narrower than
the tool** at the chosen size — those strokes physically can't be resolved. Fix by
making the artwork bigger, the strokes thicker, or the tip smaller. This is measured
as the ink area lost to a morphological opening by the tool disk.

### Output format

Every exported file follows our conventions:

```
; Elyton ECM - RASTER FILL (boustrophedon)
; Source: logo.png  |  Size: 45.00 x 9.53 mm  |  scale 0.0920 mm/px
; Tool 1.27 mm  |  stepover 0.635 mm (50% of tool)  |  radius-compensated: yes
; Cut F60  |  Travel F1600  |  Z travel 2.00  |  Z cut 0.00  |  Passes: 1
; ABSOLUTE (G90), millimeters (G21). Origin (0,0) = bottom-left of artwork box, Y up.
G21
G90
...
```

**Always absolute (G90), always mm (G21).** The app will never emit relative mode —
a G91 file once drifted our toolpath over a meter sideways. Each cut segment is:
rapid to start at Z travel → plunge → cut at cut feed → lift.

Zero the machine with the tip at the **bottom-left corner of where the artwork box
should sit** on the plate, at cutting height.

---

## Tab 2: Simulator

Opens **any** .gcode file (ours or third-party) and shows what it will actually do
before the machine runs it.

### Loading

- **Open .gcode…** or **Paste from clipboard**.
- Parses G0/G1 (X/Y/Z/F), G90/**G91**, G20/G21, G92, G28, `;` and `()` comments.
  Everything is reconstructed to absolute mm, so relative or inch files render
  correctly. Arcs (G2/G3) are not rendered — you'll get a warning listing what
  was skipped.

### Render & checks panel

| Control | What it does |
|---|---|
| **Tool width** | Diameter used to draw cut moves (visual only). |
| **Bed W / H** | Machine travel (default 220×220). The check fires if the toolpath is bigger than this. |
| **Expected Z cut / travel** | Our Z convention (0 / 2). Any Z outside this band ±0.5 mm triggers a warning — catches plunges into the plate and runaway lifts. |

### Stats readout

Extents (W×H mm plus X/Y min..max), Z range, cut distance and segment count,
travel distance and rapid count, total Z motion, estimated run time (from the
file's own feed rates), and the detected mode (G90 absolute vs **G91 RELATIVE**,
mm vs inches).

### Warnings it catches

- **Relative mode (G91)** — always flagged as a caution.
- **Open loops in relative files** — in a well-formed program each plunge…lift
  group returns to its start point. The simulator reports how many loops don't
  close and the **accumulated drift** in mm. (Run
  `examples/elyton_logo_contour_parallel_mirrored_corrected.gcode` to see the
  real incident: 91 open loops, ~960 mm of drift.)
- **Toolpath exceeds bed travel.**
- **Z outside the expected cut/travel band.**
- **No units command** (assumes mm).

### Playback

- **▶ Play / ⏸ Pause**, speed selector (1× to 500× real time).
- Scrub bar seeks anywhere in the program.
- Light gray = not yet cut, blue = already cut, red circle = tool position,
  readout shows elapsed/total time and move N of M — so you can watch the order
  of operations before committing metal to it.

---

## Files

| File | What it is |
|---|---|
| `app.py` | Entry point; the tabbed main window. |
| `toolpath.py` | Pure generator logic: mask, compensation, raster/trace, G-code emit. |
| `generator.py` | Generator tab UI. |
| `gcodesim.py` | Pure parser/analyzer: G-code → absolute moves + warnings. |
| `simulator.py` | Simulator tab UI + playback. |
| `pathview.py` | Shared mm-unit canvas (grid, origin, zoom/pan). |
| `test_toolpath.py`, `test_gcodesim.py` | Self-checks, no hardware needed. |
| `examples/` | Reference outputs, incl. the drifted G91 file the simulator flags. |

## Coming in Phase 3

Machine control tab: direct sender for the SKR Mini E3 V3.0 (connect, jog, home,
zero, run with progress/pause/abort, raw G-code console) plus a stubbed live
current/gap panel for the future custom Z PCB.
