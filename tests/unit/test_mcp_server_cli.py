"""mcp_server CLI 与 HTTP 模式配置（issue #19）：默认 stdio、HTTP 默认 eager 预热、
健康路由、DNS-rebinding 防护。不真正起服务。
"""
import os

from memory_agent import mcp_server  # noqa: E402


def test_defaults_are_stdio_loopback():
    args = mcp_server._build_parser().parse_args([])
    assert args.transport == "stdio"
    assert args.host == "127.0.0.1"
    assert args.port == 8765
    assert args.path == "/mcp"
    assert args.warmup is None


def test_http_parses_host_port_and_path():
    args = mcp_server._build_parser().parse_args(
        ["--transport", "http", "--host", "0.0.0.0", "--port", "9999", "--path", "/rpc"]
    )
    assert (args.transport, args.host, args.port, args.path) == ("http", "0.0.0.0", 9999, "/rpc")


def test_warmup_defaults_lazy_for_stdio(monkeypatch):
    monkeypatch.delenv("MEMORY_WARMUP", raising=False)
    args = mcp_server._build_parser().parse_args([])
    assert mcp_server._resolve_warmup(args) is False


def test_warmup_defaults_eager_for_http(monkeypatch):
    monkeypatch.delenv("MEMORY_WARMUP", raising=False)
    args = mcp_server._build_parser().parse_args(["--transport", "http"])
    assert mcp_server._resolve_warmup(args) is True


def test_explicit_flags_override_transport_defaults():
    http_no_warm = mcp_server._build_parser().parse_args(["--transport", "http", "--no-warmup"])
    assert mcp_server._resolve_warmup(http_no_warm) is False
    stdio_warm = mcp_server._build_parser().parse_args(["--warmup"])
    assert mcp_server._resolve_warmup(stdio_warm) is True


def test_transport_security_allows_only_loopback():
    settings = mcp_server._transport_security("127.0.0.1", 8765)
    assert settings.enable_dns_rebinding_protection is True
    assert "127.0.0.1:8765" in settings.allowed_hosts
    assert "localhost:8765" in settings.allowed_hosts


def test_health_route_is_registered():
    paths = {route.path for route in mcp_server.mcp._custom_starlette_routes}
    assert "/health" in paths


def test_write_pid_file_records_and_removes(monkeypatch, tmp_path):
    path = tmp_path / "daemon-8765.pid"
    monkeypatch.setattr(mcp_server, "daemon_pid_path", lambda port: str(path))

    mcp_server._write_pid_file(8765)
    assert path.read_text(encoding="utf-8") == str(os.getpid())

    mcp_server._remove_pid_file(str(path))
    assert not path.exists()
