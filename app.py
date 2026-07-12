"""Elyton ECM desktop app. Run: python app.py
Tabs: G-code Generator (Phase 1); Simulator and Machine Control follow in Phases 2-3."""
import sys

from PySide6.QtWidgets import QApplication, QMainWindow, QTabWidget

from generator import GeneratorTab
from simulator import SimulatorTab


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Elyton ECM")
        tabs = QTabWidget()
        tabs.addTab(GeneratorTab(), "G-code Generator")
        tabs.addTab(SimulatorTab(), "Simulator")
        self.setCentralWidget(tabs)
        self.resize(1200, 800)


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
