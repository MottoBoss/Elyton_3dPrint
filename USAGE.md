# Elyton ECM — User Guide

Desktop app for our ECM-converted Ender 3. The electrode never touches the metal:
depth comes from **dwell time and current**, not commanded Z. Feed rate is the depth
knob (slower feed = deeper etch).

**Z never moves.** The cut is switched electrically, not geometrically: `ecm_on`
before a cut stretch, `ecm_off` before every rapid. Generated programs contain no Z
word at all — you park the tip at the working gap once and it stays there.

> Requires the `ecm_on` / `ecm_off` macros to exist in the Klipper config. Give the
> output pin a `shutdown_value` of off so an E-STOP or klippy error de-energizes it —
> the app can't guarantee that from the host side.

All feed rates in the app are **mm/s**. G-code `F` words are mm/min (firmware has no
mm/s mode), so the exported file shows `F60` where the UI says 1 mm/s. The header line
prints both.

Output is **relative (G91) by default**, and safely so — see
[About relative mode](#about-relative-mode) for why, and for the checkbox that turns
it off. Also: two `ecm_on`s or two `ecm_off`s in a row can never be emitted; the
simulator flags them in any file that has them.

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
3. Set the output size, pick a mode, watch the **Toolpath** preview.
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
| **Tool diameter** | Syringe/electrode tip diameter (default 1.2 mm). We swap tips — always set this. |
| **Mode** | Three ways to cover the same ink — see the table below. |
| **Stepover** | Raster and Contour fill. Line spacing as % of tool diameter. Default 50% = adjacent lines overlap half the tool for solid coverage. |
| **Simplify tol.** | Contour fill and Trace. Polygon simplification tolerance in mm; higher = fewer points, coarser curves. Note the chords cut *across* concave boundaries, so the etch can run up to this much outside the artwork — keep it ≤ 0.1 mm on anything with curves. |
| **Compensate tool radius** | **On (default)**: toolpath is inset by half the tool diameter so the *etched* result matches the artwork dimensions. Features narrower than the tool disappear (the warning below tells you how much). **Off**: the tool center rides the ink boundary — every feature grows ~0.6 mm per side with the 1.2 tip. |

### The three modes

| Mode | What it does | ECM cycles |
|---|---|---|
| **Raster fill** | Boustrophedon scanlines (back-and-forth, alternating direction) across every ink region. Simple and predictable, but the ECM switches off and on at the end of every single scanline. | one per scanline (hundreds) |
| **Contour fill** | Same solid result, built the other way round: trace the outline, then step inward one stepover at a time — concentric rings until the region is used up. It always walks to the *nearest* ring left, so it spirals inward, and the hop from one ring to the next **stays ECM-on** — it's a stepover-long move through material that was getting etched anyway. | one per region (a handful) |
| **Trace outline** | Boundaries only, no fill: outer edges **and** holes as closed loops. | one per loop |

A run only breaks (ECM off, rapid, ECM on) when the next ring is somewhere the tool
can't reach with the current on: a separate region, across a gap, or simply further
than two stepovers away. That last rule matters — a long ECM-on hop crosses ground
that has already been etched and deepens it, so the generator would rather spend a
cycle than leave a double-depth scar across your part. (`HOP_STEPOVERS` in
`toolpath.py` if you ever want the opposite trade.)

Pick **Contour fill** unless you have a reason not to. Measured on a 45 mm logo with
a hole in it: **95 ECM cycles → 6**, rapid distance cut to a tenth, and *more* ink
covered (99.8% vs 97.0%, since rings follow the outline instead of stair-stepping
it).

What it costs is cut distance, and how much depends on the artwork: **5–15% on solid
shapes** (big fills, plates, blobs), but **up to ~2× on thin strokes** — text and line
art, where every stroke has both of its edges traced instead of a few scanlines
crossing it. So on a text logo, contour fill means ~20× fewer ECM cycles for roughly
double the run time. The generator prints both numbers (ECM cycles and estimated
time) before you export — check them and decide per job.

### Feeds

| Control | What it does |
|---|---|
| **Cut feed** | Speed of cutting moves in **mm/s** (default 1.0 → `F60`). This is the depth knob: slower = more dwell = deeper. |
| **Travel feed** | Speed of rapids in **mm/s** (default 25 → `F1500`). |
| **Passes** | Repeats the whole program N times for more dwell/depth. |
| **Relative moves (G91)** | **On by default.** Every move is a delta. Uncheck for absolute (G90) output. See below — this is safe here, and it is safe for a specific reason. |

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
; Elyton ECM - CONTOUR FILL (concentric offsets)
; Source: logo.png  |  Size: 45.00 x 9.53 mm  |  scale 0.0920 mm/px
; Tool 1.20 mm  |  stepover 0.600 mm (50% of tool)  |  simplify tol 0.10 mm  |  radius-compensated: yes
; Cut 1 mm/s (F60)  |  Travel 25 mm/s (F1500)  |  Passes: 1  |  ECM cycles: 3
; RELATIVE (G91), millimeters (G21). Deltas are measured from the commanded position, so they cannot drift.
; Starts and ends at (0,0) = bottom-left of the artwork box, Y up, ECM off.
; No Z motion: the cut is gated by ecm_on / ecm_off, not by Z.
G21
G91
ecm_off
G0 X3.456 Y10.842 F1500
ecm_on
G1 X0.635 Y0.000 F60
...
ecm_off
G0 X-14.231 Y-8.402 F1500  ; back to start
```

Each cut stretch is: `ecm_off` → rapid to the start → `ecm_on` → cut at cut feed →
`ecm_off`. The file opens with `ecm_off`, ends with `ecm_off`, closes with a rapid
back to (0,0), and contains no Z word anywhere. **The gates always alternate** —
`ecm_on` twice running (or `ecm_off` twice) is impossible by construction, because
every toggle goes through one state-tracking function that drops a redundant one.

### About relative mode

We got burned by G91 once: a file drifted the toolpath over a meter sideways. That
happened because each move's delta was measured from where the tool was *supposed*
to be, then rounded to 3 decimals — so every move donated up to half a micron of
error to a total that never got corrected.

This generator measures each delta from the position the **machine believes it is
at**: the running sum of the deltas already sent, at the same 3 decimals the file
carries. Any rounding is therefore corrected by the very next move. Measured over
200 passes of a real toolpath, error stays flat at **0.475 µm**; the old way reaches
0.2 mm and keeps climbing. `test_toolpath.py` re-parses the generated G91 with the
simulator's own parser and fails if a single point lands more than 1 nm away from
the absolute-mode output.

Uncheck **Relative moves** for plain G90 if you ever want it — the geometry is
identical, and the simulator will tell you so.

Zero the machine with the tip at the **bottom-left corner of where the artwork box
should sit** on the plate, at the working gap. Z is where you leave it. In relative
mode that corner is the *only* reference the file has, so set it before every run —
and because the program returns there when it finishes, you can run it twice without
re-zeroing.

---

## Tab 2: Simulator

Opens **any** .gcode file (ours or third-party) and shows what it will actually do
before the machine runs it.

### Loading

- **Open .gcode…** or **Paste from clipboard**.
- Parses G0/G1 (X/Y/Z/F), G90/**G91**, G20/G21, G92, G28, `ecm_on`/`ecm_off`,
  `;` and `()` comments.
  Everything is reconstructed to absolute mm, so relative or inch files render
  correctly. Arcs (G2/G3) are not rendered — you'll get a warning listing what
  was skipped.

### Render & checks panel

| Control | What it does |
|---|---|
| **Tool width** | Diameter used to draw cut moves (visual only). |
| **Bed W / H** | Machine travel (default 220×220). The check fires if the toolpath is bigger than this. |
| **Expected Z cut / travel** | Only matters for third-party files that do move Z (0 / 2 is the old convention). Our own files have no Z at all, so they sit at 0 and never trip it. |

### Stats readout

Extents (W×H mm plus X/Y min..max), Z range, cut distance and segment count,
travel distance and rapid count, total Z motion, **ECM on/off cycles**, estimated
run time (from the file's own feed rates), and the detected mode (G90 absolute vs
**G91 RELATIVE**, mm vs inches).

### Warnings it catches

- **Relative mode (G91)** — flagged as a caution for *third-party* files. Suppressed
  for files carrying our own `; Elyton ECM` header, since those compute their deltas
  drift-free and close out at the origin. Check the reconstructed extents in the
  stats readout if you want to confirm it for yourself.
- **Back-to-back ECM toggles** — two `ecm_on` (or two `ecm_off`) in a row, with the
  line numbers. Our generator cannot produce these; a hand-edited or spliced file
  can, and the controller does not survive it.
- **Open loops in relative files** — needs Z plunges to detect, so it only fires on
  older/third-party files. In a well-formed one each plunge…lift
  group returns to its start point. The simulator reports how many loops don't
  close and the **accumulated drift** in mm. (Run
  `examples/elyton_logo_contour_parallel_mirrored_corrected.gcode` to see the
  real incident: 91 open loops, ~960 mm of drift.)
- **Program ends with the ECM ON** — an `ecm_on` with no `ecm_off` after the last
  cut. Never run that file; fix the generator or the file first.
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

## Tab 3: Machine Control

Replaces the Mainsail workflow. The board stays on Klipper; the app talks HTTP to
**Moonraker** (Klipper's API service, running wherever Mainsail runs — for us,
this laptop). Nothing to reflash.

### Connecting

Enter the Moonraker address and hit **Connect**. Default is
`http://localhost:7125`; if you give a bare host (like just `localhost`), the
app tries port 7125 automatically. The status label shows klippy's state and,
once connected, the current print state. The address is remembered between runs.

**E-STOP** halts klippy instantly (motors dead, print gone). Klipper then refuses
everything until you press **FW restart**. That's Klipper by design. It does *not*
route through `ecm_off` — nothing host-side can outrun a shutdown, so the ECM pin
needs its own `shutdown_value` in the Klipper config.

### Jog / home / zero

- **X±/Y±/Z±** buttons with a step selector (0.1 / 1 / 10 mm). Jogs are sent as
  **absolute** moves (the app reads the current position, then commands
  position+step) — this app never sends G91, anywhere.
- Klipper refuses to move before homing — so **Home all** or **Home XY** first;
  the error message shows in the console if you forget.
- **Set origin here (G92 X0 Y0 Z0)**: our standard workflow — jog the tip to
  the bottom-left corner of where the artwork goes, at the working gap, then
  press this. All generated G-code assumes exactly that origin, and never
  touches Z again.
- **ECM on / ECM off**: manual gate, for setup and for panic. Same macros the
  generated files call.

### Running a program

1. **Load .gcode…** — the file automatically opens in the Simulator tab so you
   can inspect and play it back first ("View in Simulator" re-opens it any time).
2. **▶ Run** uploads the file to Moonraker and starts it.
3. Progress bar plus *line N of M, %, elapsed, remaining*, and live X/Y/Z
   position, all polled from klippy every 0.7 s.
4. **⏸ Pause** pauses the print *and* sends `ecm_off` — a paused run would
   otherwise stand still with current flowing and burn a pit. **▶ Resume**
   restarts motion but does not re-energize: the etch comes back at the file's
   next `ecm_on`, so the stretch you paused inside will be under-etched.
5. **■ Stop (ECM off)** cancels the print, then sends `ecm_off`. Cancel goes
   first on purpose: it clears the queued moves, whereas an `ecm_off` sent into
   a running print would sit behind the whole remaining buffer.

### Console

Type raw G-code, Enter sends it. The log shows what you sent plus klippy's
responses/errors (polled from Moonraker's gcode store — the same stream
Mainsail's console shows).

### Z gap / current panel (stub)

Placeholder for the future custom Z PCB that will sense current and hold the
tip-to-metal gap. The panel reads from a pluggable data source; today only a
**Mock PCB** source exists (fake sine-wave data proving the display works).
When the real PCB arrives, we add one class to `datasources.py` with a
`read()` method for whatever transport it speaks — nothing else changes.

---

## Files

| File | What it is |
|---|---|
| `app.py` | Entry point; the tabbed main window. |
| `toolpath.py` | Pure generator logic: mask, compensation, raster/contour/trace, G-code emit. |
| `generator.py` | Generator tab UI. |
| `gcodesim.py` | Pure parser/analyzer: G-code → absolute moves + warnings. |
| `simulator.py` | Simulator tab UI + playback. |
| `pathview.py` | Shared mm-unit canvas (grid, origin, zoom/pan). |
| `machine.py` | Moonraker HTTP client (stdlib only). |
| `sender.py` | Machine Control tab UI. |
| `datasources.py` | Interface + mock for the future Z-PCB current/gap feed. |
| `test_toolpath.py`, `test_gcodesim.py`, `test_machine.py` | Self-checks, no hardware needed. |
| `examples/` | Older reference outputs (pre-ECM-gating, so they still plunge Z), incl. the drifted G91 file the simulator flags. |

## If we ever switch to Marlin

Reflashing the SKR Mini E3 V3.0 to Marlin would let the app drive the board
directly over USB serial (pyserial, ok/ack flow control) with no Moonraker in
the middle. The sender's actions all go through `machine.py`'s small client
class, so that switch means writing one `MarlinClient` with the same methods —
the UI doesn't change.
