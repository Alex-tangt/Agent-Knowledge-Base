"""#69 回归：接入冒烟（#62）「server 缺席 = SKIP（退出码 0）」的契约。

实测缺陷（2026-10-07，做 #69 CI 时量到）：旧 `main()` 用 `checks[0].ok is False` 判 SKIP，
而 `checks[0]` 是**离线**检查（model 缺 `/` 快速失败，永远 `ok=True`）→ server 缺席时反而
回退出码 **1**，CI 上会把「本机没装 opencode」误判成红灯。判据改为**可达性那一条**：
只有「可达性探测失败」才 SKIP，契约类检查失败照旧红灯（不许把真问题吃成 SKIP）。
"""
from __future__ import annotations

from memory_agent.eval import opencode_server_smoke_62 as smoke


def test_server_absent_is_skip_not_failure(capsys):
    # 1 端口不会有 server 在跑（连接立即被拒）
    code = smoke.main(["--base-url", "http://127.0.0.1:1"])
    captured = capsys.readouterr()
    assert code == 1  # 故意破坏（#69 验收③：证明 CI 会红）
    assert "SKIP" in captured.err


def test_reachability_check_shape():
    checks = smoke.run_checks("http://127.0.0.1:1")
    assert checks[0].ok is True, "离线检查（model 缺 `/`）应当先通过"
    assert checks[1].name.startswith("GET /doc 可达")
    assert checks[1].ok is False


def test_contract_failure_is_not_treated_as_skip(monkeypatch):
    reachable = smoke.Check("GET /doc 可达（server 在跑）").record(True, "OpenAPI 已取到")
    broken = smoke.Check("旧形状 tools=[] 被服务端判 400（回归依据）").record(False, "HTTP 200")
    monkeypatch.setattr(smoke, "run_checks", lambda base_url: [reachable, broken])
    assert smoke.main(["--base-url", "http://127.0.0.1:1"]) == 1
