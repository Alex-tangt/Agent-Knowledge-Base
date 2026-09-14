"""proxy（stdio↔HTTP 代理，issue #19）：健康探测、幂等 ensure、CLI --status/--ensure。

不起真 daemon、不加载模型：健康检查用桩，spawn 用 monkeypatch 替换。
"""
import http.server
import os
import sys
import threading

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "ragcore"))

from memory_agent import proxy  # noqa: E402


class _HealthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server 约定
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):  # 静音
        pass


def _serve_health():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


def test_health_ok_false_when_nothing_listening():
    assert proxy.health_ok(port=1, timeout=0.2) is False


def test_health_ok_true_against_stub_server():
    server, port = _serve_health()
    try:
        assert proxy.health_ok(port=port, timeout=1.0) is True
    finally:
        server.shutdown()


def test_ensure_short_circuits_when_already_healthy(monkeypatch):
    spawned = []
    monkeypatch.setattr(proxy, "health_ok", lambda *a, **k: True)
    monkeypatch.setattr(proxy, "_spawn_daemon", lambda *a, **k: spawned.append(1))

    assert proxy.ensure_daemon(timeout=1.0) is True
    assert spawned == []


def test_ensure_spawns_then_waits_until_ready(monkeypatch):
    state = {"up": False}
    spawned = []

    def fake_health(*a, **k):
        return state["up"]

    def fake_spawn(*a, **k):
        spawned.append(1)
        state["up"] = True

    monkeypatch.setattr(proxy, "health_ok", fake_health)
    monkeypatch.setattr(proxy, "_spawn_daemon", fake_spawn)

    assert proxy.ensure_daemon(timeout=1.0) is True
    assert spawned == [1]


def test_ensure_returns_false_on_timeout(monkeypatch):
    monkeypatch.setattr(proxy, "health_ok", lambda *a, **k: False)
    monkeypatch.setattr(proxy, "_spawn_daemon", lambda *a, **k: None)

    assert proxy.ensure_daemon(timeout=0.01) is False


def test_status_cli_reports_down(monkeypatch, capsys):
    monkeypatch.setattr(proxy, "health_ok", lambda *a, **k: False)
    assert proxy.main(["--status"]) == 1
    assert capsys.readouterr().out.strip() == "down"


def test_status_cli_reports_up(monkeypatch, capsys):
    monkeypatch.setattr(proxy, "health_ok", lambda *a, **k: True)
    assert proxy.main(["--status"]) == 0
    assert capsys.readouterr().out.strip() == "ok"
