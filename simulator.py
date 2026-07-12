"""Phase 2 tab: open/paste any .gcode, render at true tool width, sanity-check,
and play it back move by move."""
import bisect
import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPainterPath, QPen
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget, QSplitter)

import gcodesim as gs
from pathview import PathView, CUT_COLOR, TRAVEL_COLOR

PENDING_COLOR = QColor(185, 195, 210)   # not-yet-cut, light
SPEEDS = [1, 5, 10, 50, 100, 500]


class SimulatorTab(QWidget):
    def __init__(self):
        super().__init__()
        self.moves = []
        self.cum_s = []      # cumulative seconds after each move
        self.total_s = 0.0
        self.t = 0.0
        self._build_ui()
        self._timer = QTimer(self, interval=33)
        self._timer.timeout.connect(self._tick)

    # ---------------- UI ----------------
    def _build_ui(self):
        self.btn_open = QPushButton("Open .gcode…")
        self.btn_open.clicked.connect(self.open_file)
        self.btn_paste = QPushButton("Paste from clipboard")
        self.btn_paste.clicked.connect(self.paste)
        self.lbl_file = QLabel("no file loaded")
        self.lbl_file.setStyleSheet("color: #666")

        opts = QGroupBox("Render && checks")
        f = QFormLayout(opts)
        self.spn_toolw = QDoubleSpinBox(minimum=0.05, maximum=10, value=1.27, decimals=2)
        self.spn_toolw.setSuffix(" mm")
        self.spn_bedw = QDoubleSpinBox(minimum=10, maximum=2000, value=220, decimals=0)
        self.spn_bedh = QDoubleSpinBox(minimum=10, maximum=2000, value=220, decimals=0)
        self.spn_zcut = QDoubleSpinBox(minimum=-50, maximum=50, value=0, decimals=2)
        self.spn_ztrav = QDoubleSpinBox(minimum=-50, maximum=50, value=2, decimals=2)
        f.addRow("Tool width", self.spn_toolw)
        f.addRow("Bed W", self.spn_bedw)
        f.addRow("Bed H", self.spn_bedh)
        f.addRow("Expected Z cut", self.spn_zcut)
        f.addRow("Expected Z travel", self.spn_ztrav)
        for w in (self.spn_toolw, self.spn_bedw, self.spn_bedh,
                  self.spn_zcut, self.spn_ztrav):
            w.valueChanged.connect(lambda *_: self.reload())

        self.lbl_stats = QLabel()
        self.lbl_stats.setWordWrap(True)
        self.lbl_warn = QLabel()
        self.lbl_warn.setWordWrap(True)
        self.lbl_warn.setStyleSheet("color: #b35900; font-weight: bold")
        self.lbl_warn.hide()

        left = QWidget()
        v = QVBoxLayout(left)
        v.addWidget(self.btn_open)
        v.addWidget(self.btn_paste)
        v.addWidget(self.lbl_file)
        v.addWidget(opts)
        v.addWidget(self.lbl_stats)
        v.addWidget(self.lbl_warn)
        v.addStretch()
        left.setMaximumWidth(340)

        self.view = PathView()
        self.btn_play = QPushButton("▶ Play")
        self.btn_play.setEnabled(False)
        self.btn_play.clicked.connect(self.toggle_play)
        self.cmb_speed = QComboBox()
        self.cmb_speed.addItems([f"{s}x" for s in SPEEDS])
        self.cmb_speed.setCurrentIndex(3)  # 50x
        self.sld_scrub = QSlider(Qt.Horizontal, minimum=0, maximum=1000)
        self.sld_scrub.valueChanged.connect(self._scrubbed)
        self.lbl_time = QLabel("--:-- / --:--")
        bar = QHBoxLayout()
        bar.addWidget(self.btn_play)
        bar.addWidget(self.cmb_speed)
        bar.addWidget(self.sld_scrub, 1)
        bar.addWidget(self.lbl_time)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.addWidget(self.view, 1)
        rv.addLayout(bar)

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        lay = QHBoxLayout(self)
        lay.addWidget(split)

    # ---------------- loading ----------------
    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open G-code", "",
                                              "G-code (*.gcode *.nc *.gc);;All files (*)")
        if not path:
            return
        with open(path, encoding="utf-8", errors="replace") as fh:
            self.text = fh.read()
        self.src = os.path.basename(path)
        self.reload()

    def paste(self):
        self.text = QGuiApplication.clipboard().text()
        self.src = "clipboard"
        self.reload()

    def reload(self):
        if not getattr(self, "text", None):
            return
        self.moves, flags, unsup = gs.parse(self.text)
        lo = min(self.spn_zcut.value(), self.spn_ztrav.value()) - 0.5
        hi = max(self.spn_zcut.value(), self.spn_ztrav.value()) + 0.5
        st, warns = gs.analyze(self.moves, flags, unsup,
                               bed=(self.spn_bedw.value(), self.spn_bedh.value()),
                               z_band=(lo, hi))
        self.lbl_file.setText(f"{self.src}  ({len(self.moves)} moves)")

        self.cum_s, acc = [], 0.0
        for m in self.moves:
            dx, dy, dz = (m.p1[i] - m.p0[i] for i in range(3))
            acc += ((dx * dx + dy * dy + dz * dz) ** 0.5) / max(m.feed, 1e-6) * 60.0
            self.cum_s.append(acc)
        self.total_s = acc

        if st["extents"]:
            e = st["extents"]
            mins = st["minutes"]
            t = f"{mins:.1f} min" if mins < 90 else f"{mins / 60:.1f} h"
            self.lbl_stats.setText(
                f"Extents: {st['size'][0]:.1f} x {st['size'][1]:.1f} mm"
                f"  (X {e[0]:.1f}..{e[1]:.1f}, Y {e[2]:.1f}..{e[3]:.1f})\n"
                f"Z range: {st['z_range'][0]:.2f}..{st['z_range'][1]:.2f} mm\n"
                f"Cut {st['cut_mm']:.0f} mm in {st['cuts']} segments\n"
                f"Travel {st['travel_mm']:.0f} mm in {st['rapids']} rapids"
                f"   |   Z motion {st['z_mm']:.0f} mm\n"
                f"Estimated time: {t}   |   Mode: {st['mode']}")
        else:
            self.lbl_stats.setText("")
        self.lbl_warn.setText("\n".join("⚠ " + w for w in warns))
        self.lbl_warn.setVisible(bool(warns))

        self._build_scene()
        self.btn_play.setEnabled(bool(self.moves))
        self.set_time(0.0)

    def _build_scene(self):
        sc = self.view.scene()
        sc.clear()
        toolw = self.spn_toolw.value()
        pend_pen = QPen(PENDING_COLOR, toolw, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        trav_pen = QPen(TRAVEL_COLOR, 0, Qt.DashLine)
        pending = QPainterPath()
        for m in self.moves:
            if (m.p1[0] - m.p0[0], m.p1[1] - m.p0[1]) == (0.0, 0.0):
                continue  # Z-only
            if m.rapid:
                sc.addLine(m.p0[0], m.p0[1], m.p1[0], m.p1[1], trav_pen)
            else:
                pending.moveTo(m.p0[0], m.p0[1])
                pending.lineTo(m.p1[0], m.p1[1])
        sc.addPath(pending, pend_pen)
        # progress layer + tool marker, updated during playback
        self.done_item = sc.addPath(QPainterPath(),
                                    QPen(CUT_COLOR, toolw, Qt.SolidLine,
                                         Qt.RoundCap, Qt.RoundJoin))
        r = toolw / 2.0
        self.marker = sc.addEllipse(-r, -r, toolw, toolw, QPen(QColor(200, 40, 40), 0))
        self.marker.setZValue(10)
        self.view.draw_origin()
        self.view.fit()

    # ---------------- playback ----------------
    def toggle_play(self):
        if self._timer.isActive():
            self._timer.stop()
            self.btn_play.setText("▶ Play")
        elif self.moves:
            if self.t >= self.total_s:
                self.set_time(0.0)
            self._timer.start()
            self.btn_play.setText("⏸ Pause")

    def _tick(self):
        speed = SPEEDS[self.cmb_speed.currentIndex()]
        self.set_time(self.t + 0.033 * speed)
        if self.t >= self.total_s:
            self.toggle_play()

    def _scrubbed(self, v):
        if self.total_s and not self._scrub_guard:
            self.set_time(self.total_s * v / 1000.0)

    _scrub_guard = False

    def set_time(self, t):
        """Position playback at absolute time t seconds.
        ponytail: rebuilds the whole progress path each call, O(moves); fine
        below ~100k moves, make it incremental if playback ever stutters."""
        self.t = max(0.0, min(t, self.total_s))
        i = bisect.bisect_left(self.cum_s, self.t) if self.cum_s else 0
        done = QPainterPath()
        pos = self.moves[0].p0[:2] if self.moves else (0, 0)
        for m in self.moves[:i]:
            if not m.rapid and (m.p0[0], m.p0[1]) != (m.p1[0], m.p1[1]):
                done.moveTo(m.p0[0], m.p0[1])
                done.lineTo(m.p1[0], m.p1[1])
        if i < len(self.moves):
            m = self.moves[i]
            t0 = self.cum_s[i - 1] if i else 0.0
            dur = self.cum_s[i] - t0
            k = (self.t - t0) / dur if dur > 0 else 1.0
            x = m.p0[0] + (m.p1[0] - m.p0[0]) * k
            y = m.p0[1] + (m.p1[1] - m.p0[1]) * k
            if not m.rapid and (m.p0[0], m.p0[1]) != (x, y):
                done.moveTo(m.p0[0], m.p0[1])
                done.lineTo(x, y)
            pos = (x, y)
        elif self.moves:
            pos = self.moves[-1].p1[:2]
        if self.moves:
            self.done_item.setPath(done)
            self.marker.setPos(*pos)
        self._scrub_guard = True
        self.sld_scrub.setValue(int(self.t / self.total_s * 1000) if self.total_s else 0)
        self._scrub_guard = False
        mv = min(bisect.bisect_left(self.cum_s, self.t) + 1, len(self.moves)) if self.moves else 0
        self.lbl_time.setText(
            f"{_mmss(self.t)} / {_mmss(self.total_s)}  |  move {mv}/{len(self.moves)}")


def _mmss(s):
    return f"{int(s // 60)}:{int(s % 60):02d}"
