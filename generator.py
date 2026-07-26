"""Phase 1 tab: image -> mask -> toolpath -> G-code."""
import datetime
import os

import numpy as np
from PIL import Image
from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QMessageBox, QPushButton, QSlider, QSpinBox, QSplitter,
    QTabWidget, QVBoxLayout, QWidget)

import toolpath as tp
from pathview import PathView

INK_MODES = ["auto", "dark", "alpha"]
MODES = ["raster", "contour", "trace"]
MODE_LABELS = ["Raster fill (boustrophedon)",
               "Contour fill (offsets, fewest ECM toggles)",
               "Trace outline (contours)"]
MODE_TITLES = {"raster": "RASTER FILL (boustrophedon)",
               "contour": "CONTOUR FILL (concentric offsets)",
               "trace": "TRACE OUTLINE (contour centerlines)"}


def load_rgba(path):
    """Any raster image via Pillow; SVG rasterized through QtSvg at 2000 px."""
    if path.lower().endswith(".svg"):
        from PySide6.QtSvg import QSvgRenderer
        r = QSvgRenderer(path)
        sz = r.defaultSize()
        if not sz.isValid():
            sz = QSize(1000, 1000)
        k = 2000.0 / max(sz.width(), sz.height())
        w, h = max(1, round(sz.width() * k)), max(1, round(sz.height() * k))
        img = QImage(w, h, QImage.Format_RGBA8888)
        img.fill(0)
        p = QPainter(img)
        r.render(p)
        p.end()
        buf = np.frombuffer(img.constBits(), np.uint8).reshape(h, img.bytesPerLine())
        return buf[:, : w * 4].reshape(h, w, 4).copy()
    return np.array(Image.open(path).convert("RGBA"))


class _FitLabel(QLabel):
    """QLabel that rescales its pixmap to fit."""
    def __init__(self):
        super().__init__(alignment=Qt.AlignCenter)
        self._pix = None
        self.setMinimumSize(100, 100)

    def set_pix(self, pix):
        self._pix = pix
        self._apply()

    def resizeEvent(self, ev):
        self._apply()
        super().resizeEvent(ev)

    def _apply(self):
        if self._pix:
            self.setPixmap(self._pix.scaled(self.size(), Qt.KeepAspectRatio,
                                            Qt.SmoothTransformation))


class GeneratorTab(QWidget):
    def __init__(self):
        super().__init__()
        self.rgba = None
        self.src_name = ""
        self.gcode_text = ""
        self._syncing = False
        self._build_ui()
        self._timer = QTimer(self, singleShot=True, interval=250)
        self._timer.timeout.connect(self.regen)

    # ---------------- UI ----------------
    def _build_ui(self):
        self.btn_open = QPushButton("Open Image…")
        self.btn_open.clicked.connect(self.open_image)
        self.lbl_file = QLabel("no image loaded")
        self.lbl_file.setStyleSheet("color: #666")

        ink = QGroupBox("Ink selection")
        f = QFormLayout(ink)
        self.cmb_ink = QComboBox()
        self.cmb_ink.addItems(["Auto detect", "Dark pixels", "Opaque pixels (alpha)"])
        self.sld_thresh = QSlider(Qt.Horizontal, minimum=1, maximum=254, value=128)
        self.lbl_thresh = QLabel("128")
        row = QHBoxLayout()
        row.addWidget(self.sld_thresh)
        row.addWidget(self.lbl_thresh)
        self.chk_invert = QCheckBox("Invert (ink = light/transparent)")
        f.addRow("Ink is", self.cmb_ink)
        f.addRow("Threshold", row)
        f.addRow(self.chk_invert)

        size = QGroupBox("Output size")
        f = QFormLayout(size)
        self.spn_w = self._dspin(1, 500, 45.0, " mm")
        self.spn_h = self._dspin(1, 500, 45.0, " mm")
        self.chk_lock = QCheckBox("Lock aspect ratio")
        self.chk_lock.setChecked(True)
        f.addRow("Width", self.spn_w)
        f.addRow("Height", self.spn_h)
        f.addRow(self.chk_lock)

        tool = QGroupBox("Tool && path")
        f = QFormLayout(tool)
        self.spn_tool = self._dspin(0.1, 10, 1.2, " mm", step=0.01, dec=2)
        self.cmb_mode = QComboBox()
        self.cmb_mode.addItems(MODE_LABELS)
        self.spn_step = self._dspin(5, 100, 50, " %", dec=0)
        self.spn_tol = self._dspin(0.0, 2.0, 0.10, " mm", step=0.05, dec=2)
        self.chk_comp = QCheckBox("Compensate tool radius (etch stays inside artwork)")
        self.chk_comp.setChecked(True)
        f.addRow("Tool diameter", self.spn_tool)
        f.addRow("Mode", self.cmb_mode)
        f.addRow("Stepover", self.spn_step)
        f.addRow("Simplify tol.", self.spn_tol)
        f.addRow(self.chk_comp)

        feeds = QGroupBox("Feeds && output")
        f = QFormLayout(feeds)
        self.spn_cutf = self._dspin(0.01, 100, 1.0, " mm/s", step=0.1, dec=2)
        self.spn_travf = self._dspin(0.1, 300, 25.0, " mm/s", step=1.0, dec=1)
        self.spn_passes = QSpinBox(minimum=1, maximum=99, value=1)
        self.chk_rel = QCheckBox("Relative moves (G91)")
        self.chk_rel.setChecked(True)
        self.chk_rel.setToolTip(
            "Deltas measured from the commanded position, so rounding cannot "
            "accumulate. Program starts and ends at (0,0).\n"
            "Uncheck for absolute (G90) output.")
        f.addRow("Cut feed", self.spn_cutf)
        f.addRow("Travel feed", self.spn_travf)
        f.addRow("Passes", self.spn_passes)
        f.addRow(self.chk_rel)

        self.btn_export = QPushButton("Export G-code…")
        self.btn_export.setEnabled(False)
        self.btn_export.clicked.connect(self.export)

        self.lbl_warn = QLabel()
        self.lbl_warn.setWordWrap(True)
        self.lbl_warn.setStyleSheet("color: #b35900; font-weight: bold")
        self.lbl_warn.hide()
        self.lbl_stats = QLabel()
        self.lbl_stats.setWordWrap(True)

        left = QWidget()
        v = QVBoxLayout(left)
        v.addWidget(self.btn_open)
        v.addWidget(self.lbl_file)
        v.addWidget(ink)
        v.addWidget(size)
        v.addWidget(tool)
        v.addWidget(feeds)
        v.addWidget(self.btn_export)
        v.addWidget(self.lbl_warn)
        v.addWidget(self.lbl_stats)
        v.addStretch()
        left.setMaximumWidth(360)

        self.view = PathView()
        self.mask_label = _FitLabel()
        right = QTabWidget()
        right.addTab(self.view, "Toolpath")
        right.addTab(self.mask_label, "Ink mask")

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        lay = QHBoxLayout(self)
        lay.addWidget(split)

        # any parameter change -> debounced regenerate
        self.spn_w.valueChanged.connect(lambda v: self._sync_size(from_width=True))
        self.spn_h.valueChanged.connect(lambda v: self._sync_size(from_width=False))
        self.chk_lock.toggled.connect(lambda on: on and self._sync_size(from_width=True))
        kick = lambda *_: self._timer.start()
        self.sld_thresh.valueChanged.connect(
            lambda v: (self.lbl_thresh.setText(str(v)), self._timer.start()))
        for w in (self.cmb_ink, self.cmb_mode):
            w.currentIndexChanged.connect(kick)
        for w in (self.chk_invert, self.chk_comp, self.chk_rel):
            w.toggled.connect(kick)
        for w in (self.spn_tool, self.spn_step, self.spn_tol, self.spn_cutf,
                  self.spn_travf, self.spn_passes):
            w.valueChanged.connect(kick)

    @staticmethod
    def _dspin(lo, hi, val, suffix, step=1.0, dec=2):
        s = QDoubleSpinBox(minimum=lo, maximum=hi, value=val, decimals=dec)
        s.setSuffix(suffix)
        s.setSingleStep(step)
        return s

    def _sync_size(self, from_width):
        if self._syncing or self.rgba is None:
            self._timer.start()
            return
        self._syncing = True
        h_px, w_px = self.rgba.shape[:2]
        if self.chk_lock.isChecked():
            if from_width:
                self.spn_h.setValue(self.spn_w.value() * h_px / w_px)
            else:
                self.spn_w.setValue(self.spn_h.value() * w_px / h_px)
        self._syncing = False
        self._timer.start()

    # ---------------- actions ----------------
    def open_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open image", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.gif *.svg);;All files (*)")
        if not path:
            return
        try:
            self.rgba = load_rgba(path)
        except Exception as e:  # corrupt/unsupported file must not crash the app
            QMessageBox.critical(self, "Could not load image", str(e))
            return
        self.src_name = os.path.basename(path)
        h, w = self.rgba.shape[:2]
        self.lbl_file.setText(f"{self.src_name}  ({w} x {h} px)")
        self.btn_export.setEnabled(True)
        self._sync_size(from_width=True)
        self.regen()

    def regen(self):
        if self.rgba is None:
            return
        mode = INK_MODES[self.cmb_ink.currentIndex()]
        mask = tp.make_mask(self.rgba, mode, self.sld_thresh.value(),
                            self.chk_invert.isChecked())
        self._show_mask(mask)

        h_px, w_px = mask.shape
        w_mm, h_mm = self.spn_w.value(), self.spn_h.value()
        sx, sy = w_mm / w_px, h_mm / h_px
        tool = self.spn_tool.value()
        rx, ry = tool / 2 / sx, tool / 2 / sy
        frac = tp.thin_fraction(mask, rx, ry)
        comp = self.chk_comp.isChecked()
        work = tp.erode_disk(mask, rx, ry) if comp else mask

        mode = MODES[self.cmb_mode.currentIndex()]
        step_mm = tool * self.spn_step.value() / 100.0
        tol = self.spn_tol.value()
        if mode == "raster":
            paths = tp.raster_paths(work, sx, sy, step_mm)
            geom = f"stepover {step_mm:.3f} mm ({self.spn_step.value():g}% of tool)"
        elif mode == "contour":
            paths = tp.offset_fill_paths(work, sx, sy, step_mm, tol)
            geom = (f"stepover {step_mm:.3f} mm ({self.spn_step.value():g}% of tool)"
                    f"  |  simplify tol {tol:.2f} mm")
        else:
            paths = tp.trace_paths(work, sx, sy, tol)
            geom = f"simplify tol {tol:.2f} mm"
        travels = [(paths[i][-1], paths[i + 1][0]) for i in range(len(paths) - 1)]

        cut_s, trav_s = self.spn_cutf.value(), self.spn_travf.value()
        rel = self.chk_rel.isChecked()
        hdr = [
            "Elyton ECM - " + MODE_TITLES[mode],
            f"Source: {self.src_name}  |  Size: {w_mm:.2f} x {h_mm:.2f} mm"
            f"  |  scale {sx:.4f} mm/px",
            f"Tool {tool:.2f} mm  |  " + geom
            + f"  |  radius-compensated: {'yes' if comp else 'no'}",
            f"Cut {cut_s:g} mm/s (F{cut_s * 60:g})  |  "
            f"Travel {trav_s:g} mm/s (F{trav_s * 60:g})"
            f"  |  Passes: {self.spn_passes.value()}"
            f"  |  ECM cycles: {len(paths) * self.spn_passes.value()}",
            ("RELATIVE (G91), millimeters (G21). Deltas are measured from the "
             "commanded position, so they cannot drift."
             if rel else
             "ABSOLUTE (G90), millimeters (G21)."),
            "Starts and ends at (0,0) = bottom-left of the artwork box, Y up, ECM off.",
            "No Z motion: the cut is gated by ecm_on / ecm_off, not by Z.",
            f"Generated {datetime.date.today().isoformat()} by Elyton ECM",
        ]
        self.gcode_text, st = tp.emit_gcode(
            paths, cut_feed=cut_s, travel_feed=trav_s, relative=rel,
            passes=self.spn_passes.value(), header_lines=hdr)

        self.view.set_paths(paths, travels, tool)

        warns = []
        if frac > 0.05:
            warns.append(
                f"⚠ {frac:.0%} of the ink is narrower than the {tool:g} mm tool "
                f"at this size - those strokes won't resolve"
                + (" and are dropped from the toolpath (compensation is on)." if comp
                   else ". The tool will overcut them."))
        if not paths:
            warns.append("⚠ Empty toolpath - nothing is wide enough for the tool.")
        self.lbl_warn.setText("\n".join(warns))
        self.lbl_warn.setVisible(bool(warns))

        m = st["minutes"]
        t = f"{m:.1f} min" if m < 90 else f"{m / 60:.1f} h"
        self.lbl_stats.setText(
            f"Output: {w_mm:.2f} x {h_mm:.2f} mm   |   {st['paths']} paths\n"
            f"Cut {st['cut_mm']:.0f} mm   travel {st['travel_mm']:.0f} mm\n"
            f"ECM on/off cycles: {st['ecm_cycles']}\nEstimated time: {t}"
            + (f"  ({self.spn_passes.value()} passes)" if self.spn_passes.value() > 1 else ""))

    def _show_mask(self, mask):
        h, w = mask.shape
        buf = np.where(mask, 40, 255).astype(np.uint8)
        img = QImage(buf.data, w, h, w, QImage.Format_Grayscale8).copy()
        self.mask_label.set_pix(QPixmap.fromImage(img))

    def export(self):
        if not self.gcode_text:
            return
        base = os.path.splitext(self.src_name)[0] or "output"
        mode = MODES[self.cmb_mode.currentIndex()]
        suggested = f"{base}_{mode}_{self.spn_w.value():g}mm.gcode"
        path, _ = QFileDialog.getSaveFileName(self, "Export G-code", suggested,
                                              "G-code (*.gcode);;All files (*)")
        if not path:
            return
        with open(path, "w", encoding="ascii") as fh:
            fh.write(self.gcode_text)
        self.lbl_file.setText(f"exported: {os.path.basename(path)}")
