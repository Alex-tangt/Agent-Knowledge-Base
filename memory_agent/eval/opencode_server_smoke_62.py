"""H 接入冒烟（#62 / ADR-0030 D7）：在**真实** `opencode serve` 上核 `OpencodeServerClient` 契约。

为什么单独一个脚本：`tests/unit/test_agent_loop.py` 用 `httpx.MockTransport` 做**离线回归**
（形状对不对），但"服务端到底收不收"只能实测。本脚本跑真 server：
1. `tools` 必须是 **map**（`{"*": False}`）——旧代码传 `[]`，服务端判 **400 BadRequest**；
2. `model` 必须 `providerID` + `modelID` 成对（只给 `modelID` → 400）；缺 `/` 时客户端**快速失败**；
3. 响应文本在 `parts[].type == "text"`，`complete()` 能取回；
4. `system` 走服务端 `system` 字段（不再混进 user 文本）。

前置：本地有在跑的 opencode server（TUI 自带，或 `opencode serve --port 4096 --pure`）。
server 缺席 = **SKIP**（退出码 0），不把冒烟变成本地必跑依赖。

用法：
    venv\\Scripts\\python.exe memory_agent/eval/opencode_server_smoke_62.py
    venv\\Scripts\\python.exe memory_agent/eval/opencode_server_smoke_62.py --sanitize-proxy-env

`--sanitize-proxy-env`：httpx 读 `NO_PROXY` 时遇到带方括号的 IPv6（如 `[::1]`）会直接
`InvalidURL`——**任何** `httpx.Client()` 都构造不出来（与 server 无关）。这是宿主 env 的写法
问题，正常应把 `NO_PROXY` 改干净；该开关只是在被测环境里临时剥掉方括号条目，让冒烟能跑完，
并把它当作**诊断结论**打出来，而不是悄悄掩盖。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from memory_agent._bootstrap import configure_utf8_stdio

DEFAULT_BASE_URL = "http://127.0.0.1:4096"

# httpx 无法解析的 NO_PROXY 条目形态：带方括号的 IPv6 字面量。
_BRACKETED = ("[", "]")


def sanitize_proxy_env() -> list[str]:
    """剥掉 `NO_PROXY` / `no_proxy` 里带方括号的条目；返回被剥掉的原文。"""
    stripped: list[str] = []
    for name in ("NO_PROXY", "no_proxy"):
        raw = os.environ.get(name)
        if not raw:
            continue
        kept = [part for part in raw.split(",") if not any(ch in part for ch in _BRACKETED)]
        removed = [part for part in raw.split(",") if any(ch in part for ch in _BRACKETED)]
        if removed:
            stripped.append(f"{name}: {','.join(removed)}")
            os.environ[name] = ",".join(kept)
    return stripped


def _sanitize_in_process() -> list[str]:
    """httpx 在 `Client()` 构造时读 env；子进程里改 `NO_PROXY` 未必生效，故这里同时兜底。"""
    return sanitize_proxy_env()


class Check:
    def __init__(self, name: str):
        self.name = name
        self.ok: bool | None = None
        self.detail = ""

    def record(self, ok: bool, detail: str = "") -> "Check":
        self.ok, self.detail = ok, detail
        return self

    def to_dict(self) -> dict:
        return {"check": self.name, "ok": self.ok, "detail": self.detail}


def run_checks(base_url: str) -> list[Check]:
    import httpx

    from memory_agent.agent_loop.llm import OpencodeServerClient

    checks: list[Check] = []

    # 0) 离线：model 缺 "/" 快速失败（配置错误不该等到服务端 400 才暴露）
    check = Check("model 缺 providerID（无 `/`）在客户端快速失败")
    try:
        OpencodeServerClient(model="qwen3.7-flash")
        check.record(False, "竟然没报错")
    except ValueError as error:
        check.record(True, str(error)[:120])
    checks.append(check)

    # 1) server 可达
    check = Check("GET /doc 可达（server 在跑）")
    try:
        doc = httpx.get(f"{base_url}/doc", timeout=10).json()
        schema = doc["paths"]["/session/{sessionID}/message"]["post"]["requestBody"]
        tools_schema = json.dumps(schema, ensure_ascii=False)
        check.record(True, "OpenAPI 已取到")
    except Exception as error:  # noqa: BLE001 - 冒烟要报出原因
        check.record(False, f"{type(error).__name__}: {error}")
        checks.append(check)
        return checks
    checks.append(check)

    # 2) 契约：OpenAPI 里 `tools` 是 map（不是数组）——旧实现的 400 由此而来
    check = Check("OpenAPI: `tools` 是 object{additionalProperties:boolean}")
    check.record('"tools":{"type":"object"' in tools_schema.replace(" ", ""),
                 tools_schema[tools_schema.find('"tools"'):][:120])
    checks.append(check)

    # 3) 真实往返：`complete()` 全路径（含 tools map + system 字段）
    client = OpencodeServerClient(base_url=base_url)
    check = Check("OpencodeServerClient.complete 真实往返取回文本")
    try:
        text = client.complete([
            {"role": "system", "content": "严格只回两个字。"},
            {"role": "user", "content": "回：可以"},
        ])
        check.record(bool(text.strip()), f"session={client.session_id} text={text!r}")
    except Exception as error:  # noqa: BLE001
        check.record(False, f"{type(error).__name__}: {error}")
    checks.append(check)

    # 4) 同一客户端复用同一 session（多轮上下文不新开会话）
    check = Check("同一 client 复用同一 session")
    try:
        session_id = client.session_id
        client.complete([{"role": "user", "content": "再回两个字：可以"}])
        check.record(client.session_id == session_id, f"session={client.session_id}")
    except Exception as error:  # noqa: BLE001
        check.record(False, f"{type(error).__name__}: {error}")
    checks.append(check)

    # 5) 回归证据：旧形状 `tools: []` 确实被服务端拒（证明修的是真问题）
    check = Check("旧形状 tools=[] 被服务端判 400（回归依据）")
    try:
        response = httpx.post(
            f"{base_url}/session/{client.session_id}/message",
            json={"parts": [{"type": "text", "text": "x"}], "tools": []}, timeout=30)
        detail = f"HTTP {response.status_code} {response.text[:80]}"
        check.record(response.status_code == 400, detail)
    except Exception as error:  # noqa: BLE001
        check.record(False, f"{type(error).__name__}: {error}")
    checks.append(check)

    return checks


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="H 接入冒烟（#62）")
    parser.add_argument("--base-url", default=os.environ.get("MEMORY_AGENT_LLM_BASE_URL")
                        or DEFAULT_BASE_URL)
    parser.add_argument("--sanitize-proxy-env", action="store_true",
                        help="临时剥掉 NO_PROXY 里带方括号的条目（宿主 env 写法问题的绕过）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = parser.parse_args(argv)

    notes: list[str] = []
    if args.sanitize_proxy_env:
        stripped = _sanitize_in_process()
        notes = [f"已剥掉 NO_PROXY 条目：{item}" for item in stripped]
        if not stripped:
            notes = ["NO_PROXY 无需处理"]

    try:
        import httpx  # noqa: F401
    except ImportError:
        print("SKIP：未安装 httpx", file=sys.stderr)
        return 0

    try:
        checks = run_checks(args.base_url)
    except Exception as error:  # httpx env 解析失败等
        message = str(error)
        if "Invalid port" in message or "URLPattern" in message:
            print("SKIP：httpx 无法解析宿主 NO_PROXY（含带方括号的 IPv6，如 [::1]）。", file=sys.stderr)
            print("      改用 --sanitize-proxy-env 复跑，或把宿主 NO_PROXY 里的 [::1] 改成 ::1。",
                  file=sys.stderr)
            return 0
        raise

    # server 缺席 = SKIP（退出码 0）：判据是**可达性那一条**，不是 checks[0]。
    # 回归依据：`checks[0]` 是离线检查（model 缺 `/` 快速失败，永远 ok），旧写法在
    # server 缺席时走到这里仍判 exit 1 —— 与"缺席即 SKIP"的契约相反（#69 实测发现）。
    unreachable = next((c for c in checks if c.name.startswith("GET /doc 可达") and c.ok is False), None)
    if unreachable is not None:
        print(f"SKIP：{args.base_url} 上没有在跑的 opencode server"
              "（`opencode serve --port 4096 --pure`）", file=sys.stderr)
        print(f"      探测失败原因：{unreachable.detail}", file=sys.stderr)
        return 0

    if args.json:
        print(json.dumps({"base_url": args.base_url, "notes": notes,
                          "checks": [c.to_dict() for c in checks]}, ensure_ascii=False, indent=2))
    else:
        for note in notes:
            print(f"note: {note}")
        print(f"base_url: {args.base_url}")
        for item in checks:
            mark = "PASS" if item.ok else "FAIL"
            print(f"  [{mark}] {item.name}\n         {item.detail}")
        passed = sum(1 for item in checks if item.ok)
        print(f"\n{passed}/{len(checks)} checks passed")

    return 0 if all(item.ok for item in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
