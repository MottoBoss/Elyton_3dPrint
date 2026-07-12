"""mm-unit toolpath canvas: grid, origin marker, wheel zoom, drag pan.
Shared by the generator preview now and the simulator in Phase 2."""
import math

import numpy as np
from PySide6.QtCore import Qt, QLineF
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView

CUT_COLOR = QColor(60, 105, 180)
TRAVEL_COLOR = QColor(175, 175, 175)


class PathView(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))  # parented, or Python GCs it
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QColor("#fafafa"))
        self.scale(1, -1)  # machine Y up

    def wheelEvent(self, ev):
        f = 1.25 if ev.angleDelta().y() > 0 else 0.8
        self.scale(f, f)

    def set_paths(self, cuts, travels, tool_dia):
        """cuts: list of Nx2 mm arrays drawn at true tool width; travels: list of
        ((x0,y0),(x1,y1)) drawn dashed."""
        sc = self.scene()
        sc.clear()
        cut_pen = QPen(CUT_COLOR, tool_dia, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        r = tool_dia / 2.0
        for p in cuts:
            if np.hypot(*(np.diff(p, axis=0).T)).sum() < 1e-6:  # dwell dot
                sc.addEllipse(p[0][0] - r, p[0][1] - r, tool_dia, tool_dia,
                              QPen(Qt.NoPen), QBrush(CUT_COLOR))
                continue
            path = QPainterPath()
            path.moveTo(*p[0])
            for x, y in p[1:]:
                path.lineTo(x, y)
            sc.addPath(path, cut_pen)
        tr_pen = QPen(TRAVEL_COLOR, 0, Qt.DashLine)
        for a, b in travels:
            sc.addLine(a[0], a[1], b[0], b[1], tr_pen)
        self.draw_origin()
        self.fit()

    def draw_origin(self):
        sc = self.scene()
        sc.addLine(0, 0, 5, 0, QPen(QColor(200, 60, 60), 0))   # X axis, red
        sc.addLine(0, 0, 0, 5, QPen(QColor(60, 160, 60), 0))   # Y axis, green

    def fit(self):
        rect = self.scene().itemsBoundingRect().adjusted(-10, -10, 10, 10)
        self.scene().setSceneRect(rect)
        self.fitInView(rect, Qt.KeepAspectRatio)

    def drawBackground(self, painter, rect):
        super().drawBackground(painter, rect)
        steps = [(10.0, QColor(0, 0, 0, 45))]
        if self.transform().m11() > 12:  # zoomed in enough for a 1 mm grid
            steps.insert(0, (1.0, QColor(0, 0, 0, 15)))
        for step, col in steps:
            painter.setPen(QPen(col, 0))
            x = math.floor(rect.left() / step) * step
            while x <= rect.right():
                painter.drawLine(QLineF(x, rect.top(), x, rect.bottom()))
                x += step
            y = math.floor(rect.top() / step) * step
            while y <= rect.bottom():
                painter.drawLine(QLineF(rect.left(), y, rect.right(), y))
                y += step
