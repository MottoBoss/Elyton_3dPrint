"""Self-check for machine.py against a fake in-process Moonraker.
Run: python test_machine.py"""
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

from machine import MoonrakerClient, MoonrakerError

RECEIVED = []


class FakeMoonraker(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        RECEIVED.append(("GET", self.path, b""))
        if self.path.startswith("/printer/info"):
            self._send({"result": {"state": "ready"}})
        elif self.path.startswith("/printer/objects/query"):
            self._send({"result": {"status": {
                "gcode_move": {"gcode_position": [10.0, 20.0, 2.0, 0.0]},
                "print_stats": {"state": "printing", "print_duration": 30.0,
                                "filename": "x.gcode"},
                "virtual_sdcard": {"progress": 0.5, "file_position": 100}}}})
        elif self.path.startswith("/server/gcode_store"):
            self._send({"result": {"gcode_store": [
                {"message": "ok", "time": 1.0, "type": "response"}]}})
        else:
            self._send({"error": {"message": "not found"}}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        RECEIVED.append(("POST", self.path, self.rfile.read(n)))
        if "/gcode/script" in self.path and "FAIL" in self.path:
            self._send({"error": {"message": "Must home axis first"}}, 400)
        else:
            self._send({"result": "ok"})


def main():
    srv = HTTPServer(("127.0.0.1", 0), FakeMoonraker)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cli = MoonrakerClient(f"http://127.0.0.1:{srv.server_port}")

    assert cli.info()["state"] == "ready"
    st = cli.query()
    assert st["gcode_move"]["gcode_position"][2] == 2.0
    assert cli.gcode_store()[0]["message"] == "ok"

    cli.gcode("G90\nG1 X15.000 F1600")
    sent = urllib.parse.unquote(RECEIVED[-1][1])
    assert "G1 X15.000 F1600" in sent
    assert "G91" not in sent, "jog must never be relative"

    cli.upload("part.gcode", "G21\nG90\nG1 X1 F60\n")
    body = RECEIVED[-1][2].decode()
    assert 'filename="part.gcode"' in body and "G1 X1 F60" in body
    assert "gcodes" in body  # root field

    cli.start_print("part.gcode")
    assert "/printer/print/start" in RECEIVED[-1][1]
    cli.pause(); cli.resume(); cli.cancel()
    assert "/printer/print/cancel" in RECEIVED[-1][1]

    # klippy errors surface as readable messages
    try:
        cli.gcode("FAIL")
        raise AssertionError("expected MoonrakerError")
    except MoonrakerError as e:
        assert "Must home axis first" in str(e)

    srv.shutdown()
    print("all checks pass")


if __name__ == "__main__":
    main()
