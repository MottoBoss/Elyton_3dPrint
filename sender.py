"""Phase 3 tab: machine control over Moonraker (Klipper stays flashed).

All HTTP runs on the Qt thread pool so homing or long scripts never freeze
the UI. Jogs are ABSOLUTE moves (query position, then G1 to pos+step) --
this app never sends G91, per the drift incident.
"""
import bisect
import os
import time
from urllib.parse import urlparse

from PySide6.QtCore import QObject, QSettings, Qt, QThreadPool, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QPlainTextEdit, QProgressBar, QPushButton, QSplitter,
    QVBoxLayout, QWidget)

from datasources import MockGapSource
from machine import MoonrakerClient, MoonrakerError

Z_LIFT_ON_ABORT = 2.0
JOG_FEED_XY, JOG_FEED_Z = 1600, 600


class _Sig(QObject):
    done = Signal(object, object)  # (result, exception)


class SenderTab(QWidget):
    def __init__(self, on_view_gcode=None):
        super().__init__()
        self.on_view_gcode = on_view_gcode
        self.cli = None
        self.text = None
        self.fname = None
        self.line_offsets = []   # cumulative byte offsets -> line N from file_position
        self.state = "?"
        self._jobs = set()
        self._polling = False
        self._store_t = None
        self.gap_source = None
        self._poll_timer = QTimer(self, interval=700)
        self._poll_timer.timeout.connect(self._poll)
        self._gap_timer = QTimer(self, interval=300)
        self._gap_timer.timeout.connect(self._gap_tick)
        self._gap_timer.start()
        self._build_ui()

    # ---------------- async plumbing ----------------
    def _async(self, fn, on_done=None):
        sig = _Sig()
        self._jobs.add(sig)

        def run():
            try:
                res, err = fn(), None
            except Exception as e:
                res, err = None, e
            sig.done.emit(res, err)

        def finish(res, err):
            self._jobs.discard(sig)
            if on_done:
                on_done(res, err)

        sig.done.connect(finish)
        QThreadPool.globalInstance().start(run)

    def _cmd(self, fn, label):
        """Fire a machine action; log it and any error to the console."""
        self._log(f"> {label}")

        def done(res, err):
            if err:
                self._log(f"!! {err}")
        self._async(fn, done)

    def _log(self, text):
        self.console.appendPlainText(text)

    # ---------------- UI ----------------
    def _build_ui(self):
        settings = QSettings("Elyton", "ECM")
        self.ed_url = QLineEdit(settings.value("moonraker_url", "http://localhost:7125"))
        self.btn_conn = QPushButton("Connect")
        self.btn_conn.clicked.connect(self._connect)
        self.lbl_status = QLabel("disconnected")
        self.btn_estop = QPushButton("E-STOP")
        self.btn_estop.setStyleSheet("background:#c0392b;color:white;font-weight:bold")
        self.btn_estop.clicked.connect(
            lambda: self.cli and self._cmd(self.cli.emergency_stop, "EMERGENCY STOP"))
        self.btn_fwrestart = QPushButton("FW restart")
        self.btn_fwrestart.clicked.connect(
            lambda: self.cli and self._cmd(self.cli.firmware_restart, "firmware restart"))
        top = QHBoxLayout()
        top.addWidget(QLabel("Moonraker:"))
        top.addWidget(self.ed_url, 1)
        top.addWidget(self.btn_conn)
        top.addWidget(self.lbl_status, 1)
        top.addWidget(self.btn_estop)
        top.addWidget(self.btn_fwrestart)

        jog = QGroupBox("Jog")
        g = QGridLayout(jog)
        mk = lambda t, ax, sgn: self._jog_btn(t, ax, sgn)
        g.addWidget(mk("Y+", 1, +1), 0, 1)
        g.addWidget(mk("X-", 0, -1), 1, 0)
        g.addWidget(mk("X+", 0, +1), 1, 2)
        g.addWidget(mk("Y-", 1, -1), 2, 1)
        g.addWidget(mk("Z+", 2, +1), 0, 3)
        g.addWidget(mk("Z-", 2, -1), 2, 3)
        self.cmb_step = QComboBox()
        self.cmb_step.addItems(["0.1", "1", "10"])
        self.cmb_step.setCurrentIndex(1)
        g.addWidget(QLabel("step (mm)"), 3, 0)
        g.addWidget(self.cmb_step, 3, 1)
        self.btn_home = QPushButton("Home all (G28)")
        self.btn_home.clicked.connect(lambda: self._script("G28", "home all", timeout=180))
        self.btn_homexy = QPushButton("Home XY")
        self.btn_homexy.clicked.connect(lambda: self._script("G28 X Y", "home XY", timeout=180))
        self.btn_zero = QPushButton("Set origin here (G92 X0 Y0 Z0)")
        self.btn_zero.clicked.connect(lambda: self._script("G92 X0 Y0 Z0", "set origin"))
        g.addWidget(self.btn_home, 4, 0, 1, 2)
        g.addWidget(self.btn_homexy, 4, 2, 1, 2)
        g.addWidget(self.btn_zero, 5, 0, 1, 4)

        runbox = QGroupBox("Program")
        v = QVBoxLayout(runbox)
        self.btn_load = QPushButton("Load .gcode…")
        self.btn_load.clicked.connect(self._load)
        self.lbl_fname = QLabel("no file")
        self.lbl_fname.setStyleSheet("color:#666")
        self.btn_view = QPushButton("View in Simulator")
        self.btn_view.setEnabled(False)
        self.btn_view.clicked.connect(
            lambda: self.on_view_gcode and self.on_view_gcode(self.text, self.fname))
        self.btn_run = QPushButton("▶ Run")
        self.btn_run.clicked.connect(self._run)
        self.btn_pause = QPushButton("⏸ Pause")
        self.btn_pause.clicked.connect(self._pause_resume)
        self.btn_stop = QPushButton("■ Stop (safe lift)")
        self.btn_stop.clicked.connect(self._stop)
        row = QHBoxLayout()
        row.addWidget(self.btn_run)
        row.addWidget(self.btn_pause)
        row.addWidget(self.btn_stop)
        self.progress = QProgressBar(maximum=100)
        self.lbl_prog = QLabel("-")
        v.addWidget(self.btn_load)
        v.addWidget(self.lbl_fname)
        v.addWidget(self.btn_view)
        v.addLayout(row)
        v.addWidget(self.progress)
        v.addWidget(self.lbl_prog)

        posbox = QGroupBox("Position (gcode coords)")
        ph = QHBoxLayout(posbox)
        self.lbl_pos = QLabel("X -    Y -    Z -")
        self.lbl_pos.setStyleSheet("font-family:Consolas,monospace; font-size:14pt")
        ph.addWidget(self.lbl_pos)

        gap = QGroupBox("Z gap / current — future PCB (stub)")
        gh = QGridLayout(gap)
        self.cmb_gap = QComboBox()
        self.cmb_gap.addItems(["None", MockGapSource.name])
        self.cmb_gap.currentIndexChanged.connect(self._gap_source_changed)
        self.lbl_current = QLabel("—")
        self.lbl_gap = QLabel("—")
        gh.addWidget(QLabel("Source"), 0, 0)
        gh.addWidget(self.cmb_gap, 0, 1)
        gh.addWidget(QLabel("Current"), 1, 0)
        gh.addWidget(self.lbl_current, 1, 1)
        gh.addWidget(QLabel("Gap"), 2, 0)
        gh.addWidget(self.lbl_gap, 2, 1)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.addWidget(jog)
        lv.addWidget(runbox)
        lv.addWidget(posbox)
        lv.addWidget(gap)
        lv.addStretch()
        left.setMaximumWidth(420)

        conbox = QGroupBox("Console")
        cv = QVBoxLayout(conbox)
        self.console = QPlainTextEdit(readOnly=True, maximumBlockCount=2000)
        self.console.setStyleSheet("font-family:Consolas,monospace")
        self.ed_cmd = QLineEdit(placeholderText="raw G-code, Enter to send")
        self.ed_cmd.returnPressed.connect(self._send_manual)
        cv.addWidget(self.console, 1)
        cv.addWidget(self.ed_cmd)

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(conbox)
        split.setStretchFactor(1, 1)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(split, 1)
        self._set_connected(False)

    def _jog_btn(self, text, axis, sign):
        b = QPushButton(text)
        b.clicked.connect(lambda: self._jog(axis, sign))
        return b

    def _set_connected(self, on):
        for w in (self.btn_home, self.btn_homexy, self.btn_zero, self.btn_run,
                  self.btn_pause, self.btn_stop, self.btn_estop,
                  self.btn_fwrestart, self.ed_cmd):
            w.setEnabled(on)
        jog_grid = self.findChildren(QPushButton)
        for b in jog_grid:
            if b.text() in ("X+", "X-", "Y+", "Y-", "Z+", "Z-"):
                b.setEnabled(on)
        self.btn_conn.setText("Disconnect" if on else "Connect")
        if not on:
            self.lbl_status.setText("disconnected")
            self._poll_timer.stop()

    # ---------------- connection ----------------
    def _connect(self):
        if self.cli:
            self.cli = None
            self._set_connected(False)
            self._log("> disconnected")
            return
        url = self.ed_url.text().strip() or "http://localhost:7125"
        if "://" not in url:
            url = "http://" + url
        candidates = [url]
        if ":" not in urlparse(url).netloc:
            candidates.append(url + ":7125")  # bare host -> Moonraker default port

        def work():
            err = None
            for c in candidates:
                cli = MoonrakerClient(c)
                try:
                    return cli, cli.info()
                except MoonrakerError as e:
                    err = e
            raise err

        self.lbl_status.setText("connecting…")

        def done(res, err):
            if err:
                self.lbl_status.setText(f"failed: {err}")
                return
            self.cli, info = res
            QSettings("Elyton", "ECM").setValue("moonraker_url", self.cli.base)
            self._set_connected(True)
            self.lbl_status.setText(f"klippy: {info.get('state', '?')}")
            self._log(f"> connected to {self.cli.base} "
                      f"(klippy {info.get('state', '?')})")
            self._store_t = None
            self._poll_timer.start()
        self._async(work, done)

    # ---------------- machine actions ----------------
    def _script(self, script, label, timeout=120):
        if self.cli:
            cli = self.cli
            self._cmd(lambda: cli.gcode(script, timeout=timeout), label)

    def _jog(self, axis, sign):
        if not self.cli:
            return
        cli = self.cli
        step = float(self.cmb_step.currentText())
        feed = JOG_FEED_Z if axis == 2 else JOG_FEED_XY
        ax = "XYZ"[axis]

        def work():  # absolute jog: read position, move to pos+step. Never G91.
            pos = cli.query()["gcode_move"]["gcode_position"]
            cli.gcode(f"G90\nG1 {ax}{pos[axis] + sign * step:.3f} F{feed}")
        self._cmd(work, f"jog {ax}{'+' if sign > 0 else '-'}{step:g}")

    def _send_manual(self):
        script = self.ed_cmd.text().strip()
        if script and self.cli:
            self.ed_cmd.clear()
            self._script(script, script)

    def _load(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load G-code", "",
                                              "G-code (*.gcode *.nc *.gc);;All files (*)")
        if not path:
            return
        with open(path, encoding="utf-8", errors="replace") as fh:
            self.text = fh.read()
        self.fname = os.path.basename(path)
        n = self.text.count("\n") + 1
        self.lbl_fname.setText(f"{self.fname}  ({n} lines)")
        self.btn_view.setEnabled(True)
        off, acc = [], 0
        for ln in self.text.splitlines(keepends=True):
            acc += len(ln.encode())
            off.append(acc)
        self.line_offsets = off
        if self.on_view_gcode:  # show it in the simulator right away
            self.on_view_gcode(self.text, self.fname)

    def _run(self):
        if not (self.cli and self.text):
            return
        cli, name, text = self.cli, self.fname, self.text

        def work():
            cli.upload(name, text)
            cli.start_print(name)
        self._cmd(work, f"run {name}")

    def _pause_resume(self):
        if not self.cli:
            return
        if self.state == "paused":
            self._cmd(self.cli.resume, "resume")
        else:
            self._cmd(self.cli.pause, "pause")

    def _stop(self):
        if not self.cli:
            return
        cli = self.cli

        def work():
            cli.cancel()
            z = cli.query()["gcode_move"]["gcode_position"][2]
            cli.gcode(f"G90\nG1 Z{z + Z_LIFT_ON_ABORT:.2f} F{JOG_FEED_Z}")
        self._cmd(work, "STOP (cancel + safe Z lift)")

    # ---------------- polling ----------------
    def _poll(self):
        if not self.cli or self._polling:
            return
        self._polling = True
        cli = self.cli

        def work():
            return cli.query(), cli.gcode_store(50)
        self._async(work, self._poll_done)

    def _poll_done(self, res, err):
        self._polling = False
        if err:
            self.lbl_status.setText(f"connection lost: {err}")
            return
        if not self.cli:
            return
        st, store = res
        x, y, z = st["gcode_move"]["gcode_position"][:3]
        self.lbl_pos.setText(f"X {x:8.3f}   Y {y:8.3f}   Z {z:7.3f}")
        ps, vs = st["print_stats"], st["virtual_sdcard"]
        self.state = ps.get("state", "?")
        self.lbl_status.setText(f"klippy ready  |  {self.state}")
        self.btn_pause.setText("▶ Resume" if self.state == "paused" else "⏸ Pause")
        printing = self.state in ("printing", "paused")
        p = vs.get("progress", 0.0) or 0.0
        self.progress.setValue(int(p * 100))
        if printing:
            fpos = vs.get("file_position", 0) or 0
            n = bisect.bisect_left(self.line_offsets, fpos) + 1 if self.line_offsets else 0
            total = len(self.line_offsets)
            dur = ps.get("print_duration", 0.0) or 0.0
            rem = dur * (1 - p) / p if p > 0.01 else 0.0
            self.lbl_prog.setText(
                f"{ps.get('filename', '')}: line {n}/{total}  |  {p:.0%}  |  "
                f"elapsed {_mmss(dur)}  remaining ~{_mmss(rem)}")
        elif self.state in ("complete", "cancelled", "error"):
            self.lbl_prog.setText(self.state)
        # console: append new klippy responses (first poll just sets the cursor)
        if store:
            if self._store_t is None:
                self._store_t = store[-1]["time"]
            else:
                for e in store:
                    if e["time"] > self._store_t:
                        pre = "" if e.get("type") == "response" else "> "
                        self._log(pre + e["message"])
                        self._store_t = e["time"]
        elif self._store_t is None:
            self._store_t = time.time()

    # ---------------- gap panel (future Z PCB) ----------------
    def _gap_source_changed(self, idx):
        self.gap_source = MockGapSource() if idx == 1 else None

    def _gap_tick(self):
        d = self.gap_source.read() if self.gap_source else None
        self.lbl_current.setText(f"{d['current_ma']:.0f} mA" if d else "—")
        self.lbl_gap.setText(f"{d['gap_um']:.0f} µm" if d else "—")


def _mmss(s):
    return f"{int(s // 60)}:{int(s % 60):02d}"
