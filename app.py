"""Elyton ECM desktop app. Run: python app.py
Tabs: G-code Generator (Phase 1), Simulator (Phase 2), Machine Control (Phase 3)."""
import sys

from PySide6.QtWidgets import QApplication, QMainWindow, QTabWidget

from generator import GeneratorTab
from sender import SenderTab
from simulator import SimulatorTab


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Elyton ECM")
        tabs = QTabWidget()
        sim = SimulatorTab()

        def view_gcode(text, name):
            sim.load_text(text, name)
            tabs.setCurrentWidget(sim)

        tabs.addTab(GeneratorTab(), "G-code Generator")
        tabs.addTab(sim, "Simulator")
        tabs.addTab(SenderTab(on_view_gcode=view_gcode), "Machine Control")
        self.setCentralWidget(tabs)
        self.resize(1280, 820)


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
