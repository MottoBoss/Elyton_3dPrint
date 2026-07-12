"""Data sources for the future custom Z PCB (current sensing / gap hold).

The transport is not decided yet, so the app only ever talks to this
interface: a class with a `name` and a `read()` returning
{"current_ma": float, "gap_um": float} or None when no sample is available.
When the real PCB lands, add one class here (serial, UDP, whatever it speaks)
and register it in sender.py's source combo -- nothing else changes.
"""
import math
import time


class MockGapSource:
    """Plausible fake data so the panel is testable before the PCB exists."""
    name = "Mock PCB"

    def read(self):
        t = time.time()
        return {"current_ma": 850 + 120 * math.sin(t * 1.3) + 30 * math.sin(t * 7.1),
                "gap_um": 60 + 15 * math.sin(t * 0.9 + 1)}
