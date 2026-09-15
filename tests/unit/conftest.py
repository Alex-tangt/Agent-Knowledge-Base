"""单元测试引导。

`ragcore` / `memory_agent` 现在是真包（ADR-0024），不再用 sys.path 垫片。
按仓库约定用 `venv\\Scripts\\python.exe -m pytest tests/unit -q` 从仓库根运行
（`python -m` 会把 CWD 加入 sys.path），或先 `pip install -e ragcore -e memory_agent`。
"""
