"""Moonraker HTTP client -- the machine link for Phase 3. Stdlib only, no Qt.

Klipper's MCU only talks to klippy; Moonraker exposes klippy over HTTP.
This client covers exactly what the sender needs: run G-code, upload+print,
pause/resume/cancel, position/progress queries, console history, e-stop.
"""
import json
import urllib.error
import urllib.parse
import urllib.request
import uuid


class MoonrakerError(RuntimeError):
    pass


class MoonrakerClient:
    def __init__(self, base_url):
        self.base = base_url.rstrip("/")

    def _req(self, path, method="GET", data=None, headers=None, timeout=10):
        req = urllib.request.Request(self.base + path, data=data,
                                     method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            try:
                msg = json.loads(body)["error"]["message"]
            except Exception:
                msg = body[:300] or str(e)
            raise MoonrakerError(msg) from None
        except OSError as e:
            raise MoonrakerError(str(e)) from None

    # -- status ------------------------------------------------------------
    def info(self):
        return self._req("/printer/info", timeout=5)["result"]

    def query(self):
        """Live position (gcode coords), print state, progress."""
        q = "gcode_move&print_stats&virtual_sdcard"
        return self._req(f"/printer/objects/query?{q}")["result"]["status"]

    def gcode_store(self, count=50):
        """Recent console commands/responses klippy produced."""
        return self._req(f"/server/gcode_store?count={count}")["result"]["gcode_store"]

    # -- actions -----------------------------------------------------------
    def gcode(self, script, timeout=120):
        """Blocks until klippy executed the script (homing can take a while)."""
        q = urllib.parse.quote(script)
        return self._req(f"/printer/gcode/script?script={q}", method="POST",
                         timeout=timeout)

    def upload(self, name, text):
        b = uuid.uuid4().hex
        body = (
            f"--{b}\r\nContent-Disposition: form-data; name=\"root\"\r\n\r\n"
            f"gcodes\r\n"
            f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"{name}\"\r\nContent-Type: text/plain\r\n\r\n{text}\r\n"
            f"--{b}--\r\n").encode()
        return self._req("/server/files/upload", method="POST", data=body,
                         headers={"Content-Type":
                                  f"multipart/form-data; boundary={b}"},
                         timeout=60)

    def start_print(self, name):
        return self._req("/printer/print/start?filename="
                         + urllib.parse.quote(name), method="POST", timeout=30)

    def pause(self):
        return self._req("/printer/print/pause", method="POST", timeout=30)

    def resume(self):
        return self._req("/printer/print/resume", method="POST", timeout=30)

    def cancel(self):
        return self._req("/printer/print/cancel", method="POST", timeout=30)

    def emergency_stop(self):
        """Kills klippy immediately; needs firmware_restart afterwards."""
        return self._req("/printer/emergency_stop", method="POST", timeout=5)

    def firmware_restart(self):
        return self._req("/printer/firmware_restart", method="POST", timeout=30)
