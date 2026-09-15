"""proxy（stdio↔HTTP 代理，issue #19）：健康探测、幂等 ensure、CLI --status/--ensure。

不起真 daemon、不加载模型：健康检查用桩，spawn 用 monkeypatch 替换。
"""
import http.server
import os
import threading
import time

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

    assert proxy.ensure_daemon(port=1, timeout=1.0) is True
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

    assert proxy.ensure_daemon(port=1, timeout=1.0) is True
    assert spawned == [1]


def test_ensure_returns_false_on_timeout(monkeypatch):
    monkeypatch.setattr(proxy, "health_ok", lambda *a, **k: False)
    monkeypatch.setattr(proxy, "_spawn_daemon", lambda *a, **k: None)

    assert proxy.ensure_daemon(port=1, timeout=0.01) is False


def test_loser_does_not_spawn_while_another_starter_holds_lock(monkeypatch):
    """抢不到锁的会话只等待，绝不再 spawn 一个 daemon（否则 N 份模型一起加载）。"""
    lock_path = proxy._lock_path(2)
    fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    spawned = []
    calls = {"n": 0}

    def fake_health(*a, **k):
        calls["n"] += 1
        return calls["n"] > 1  # 顶部检查 False，进入等待分支后变 True

    monkeypatch.setattr(proxy, "health_ok", fake_health)
    monkeypatch.setattr(proxy, "_spawn_daemon", lambda *a, **k: spawned.append(1))
    try:
        assert proxy.ensure_daemon(port=2, timeout=1.0) is True
    finally:
        os.close(fd)
        os.remove(lock_path)
    assert spawned == []


def test_stale_lock_is_reclaimed(monkeypatch):
    lock_path = proxy._lock_path(3)
    fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.close(fd)
    old = time.time() - proxy.STALE_LOCK_SECONDS - 10
    os.utime(lock_path, (old, old))
    spawned = []

    def fake_health(*a, **k):
        return bool(spawned)

    monkeypatch.setattr(proxy, "health_ok", fake_health)
    monkeypatch.setattr(proxy, "_spawn_daemon", lambda *a, **k: spawned.append(1))
    try:
        assert proxy.ensure_daemon(port=3, timeout=1.0) is True
    finally:
        if os.path.exists(lock_path):
            os.remove(lock_path)
    assert spawned == [1]


def test_status_cli_reports_down(monkeypatch, capsys):
    monkeypatch.setattr(proxy, "health_ok", lambda *a, **k: False)
    assert proxy.main(["--status"]) == 1
    assert capsys.readouterr().out.strip() == "down"


def test_status_cli_reports_up(monkeypatch, capsys):
    monkeypatch.setattr(proxy, "health_ok", lambda *a, **k: True)
    assert proxy.main(["--status"]) == 0
    assert capsys.readouterr().out.strip() == "ok"


def test_stop_reports_down_when_not_running(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(proxy, "health_ok", lambda *a, **k: False)
    monkeypatch.setattr(proxy, "daemon_pid_path", lambda port: str(tmp_path / "d.pid"))
    assert proxy.main(["--stop"]) == 0
    assert capsys.readouterr().out.strip() == "down"


def test_stop_kills_daemon_via_pid_file(monkeypatch, tmp_path):
    pid_file = tmp_path / "d.pid"
    pid_file.write_text("4321", encoding="utf-8")
    state = {"up": True, "killed": []}

    def fake_health(*a, **k):
        return state["up"]

    def fake_kill(pid):
        state["killed"].append(pid)
        state["up"] = False

    monkeypatch.setattr(proxy, "health_ok", fake_health)
    monkeypatch.setattr(proxy, "daemon_pid_path", lambda port: str(pid_file))
    monkeypatch.setattr(proxy, "_kill_pid", fake_kill)

    assert proxy.stop_daemon(timeout=1.0) == "stopped"
    assert state["killed"] == [4321]
    assert not pid_file.exists()


def test_stop_fails_without_pid_file(monkeypatch, tmp_path):
    monkeypatch.setattr(proxy, "health_ok", lambda *a, **k: True)
    monkeypatch.setattr(proxy, "daemon_pid_path",
                        lambda port: str(tmp_path / "missing.pid"))
    assert proxy.stop_daemon(timeout=0.5) == "failed"
