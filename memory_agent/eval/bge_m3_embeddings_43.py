"""issue #43 验收：共享 daemon 暴露 BGE-M3 的 OpenAI 兼容 embeddings 端点。

要证明的事（与 issue #43 验收对应）：
1. `POST /v1/embeddings` 返回 OpenAI 形状、dim=1024、单位范数；支持 string 与 list。
2. authn 与 `/mcp` 同源：无 token 配置时零配置直连；带无效 Bearer 被拒。
3. 走 **DeepTutor 自身**的 `EmbeddingClient`（系统 Python）能真的拿到 BGE-M3 向量
   ——即写出的 `model_catalog.json` 端到端可用，不是只校验配置。
4. `connect.py` 幂等写 embedding profile，**保留 LLM profile 与其 key**。
5. venv 路径（修复 `transformers` 后）能起 daemon 并加载 BGE-M3（回归修复）。

临时 KB / 索引 / DeepTutor home + 独立端口，不污染真实语料与真 daemon。用法：
    $env:PYTHONPATH="<worktree>"; venv\\Scripts\\python.exe memory_agent/eval/bge_m3_embeddings_43.py
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _ROOT)

from memory_agent import connect  # noqa: E402

PY = sys.executable
WORK = os.path.join(os.environ.get("TEMP", "/tmp"), "opencode",
                    f"bge-m3-embeddings-43-{int(time.time())}")
INDEX = os.path.join(WORK, "index")
KB = os.path.join(WORK, "kb")
DT_HOME = os.path.join(WORK, "deeptutor-home")
OPENCODE_HOME = os.path.join(WORK, "opencode-home")
LOG = os.path.join(WORK, "daemon.log")
HOST = "127.0.0.1"
PORT = 0
EMBEDDINGS_URL = ""
RESULTS_FILE = os.path.join(_HERE, "bge_m3_embeddings_43_results.md")

RESULTS: list[str] = []
CHECKS: list[tuple[str, bool, str]] = []


def note(msg: str = "") -> None:
    print(msg, flush=True)
    RESULTS.append(msg)


def check(name: str, passed: bool, detail: str = "") -> bool:
    CHECKS.append((name, bool(passed), detail))
    note(f"  [{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return bool(passed)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((HOST, 0))
        return sock.getsockname()[1]


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _post_embeddings(payload: dict, headers: dict | None = None, timeout: float = 180):
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        EMBEDDINGS_URL, data=data,
        headers={"Content-Type": "application/json", **(headers or {})}, method="POST",
    )
    try:
        with _opener().open(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(body)
        except ValueError:
            return exc.code, {"raw": body}


def health_ok(timeout: float = 1.0) -> bool:
    try:
        with _opener().open(f"http://{HOST}:{PORT}/health", timeout=timeout) as response:
            return response.status == 200
    except Exception:  # noqa: BLE001
        return False


def _daemon_env() -> dict:
    env = dict(os.environ)
    env.update({
        "AGENT_KB_DIR": KB,
        "MEMORY_INDEX_DIR": INDEX,
        "MEMORY_READONLY_ROOTS": "",
        "MEMORY_MCP_HOST": HOST,
        "MEMORY_MCP_PORT": str(PORT),
        "MEMORY_MCP_PATH": "/mcp",
        "MEMORY_DAEMON_LOG": LOG,
        # 让子进程 import 到本 worktree 的 ragcore/memory_agent（editable 指向主树）
        "PYTHONPATH": _ROOT,
    })
    env.pop("MEMORY_AUTH_TOKENS", None)
    return env


def start_daemon() -> subprocess.Popen:
    server = os.path.join(_ROOT, "memory_agent", "mcp_server.py")
    creationflags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                     | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    log = open(LOG, "wb")
    proc = subprocess.Popen(
        [PY, server, "--transport", "http", "--host", HOST, "--port", str(PORT),
         "--no-warmup"],
        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, cwd=_ROOT,
        env=_daemon_env(), creationflags=creationflags, close_fds=True,
    )
    log.close()
    for _ in range(240):
        if proc.poll() is not None:
            raise RuntimeError(f"daemon 提前退出（rc={proc.returncode}），见 {LOG}")
        if health_ok(0.5):
            return proc
        time.sleep(0.25)
    raise RuntimeError("daemon 未就绪")


def _kill_tree(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)


def setup_workspace() -> None:
    for path in (KB, INDEX, DT_HOME, OPENCODE_HOME):
        os.makedirs(path, exist_ok=True)
    # 预置一个带 key 的 llm profile：验证合并**不碰**它。
    settings_dir = os.path.dirname(connect.deeptutor_model_catalog_path(DT_HOME))
    os.makedirs(settings_dir, exist_ok=True)
    with open(connect.deeptutor_model_catalog_path(DT_HOME), "w", encoding="utf-8") as fh:
        json.dump({"version": 1, "services": {"llm": {
            "active_profile_id": "llm-profile-default",
            "active_model_id": "llm-model-default",
            "profiles": [{
                "id": "llm-profile-default", "binding": "deepseek",
                "base_url": "https://api.deepseek.com", "api_key": "sk-keep-me",
                "models": [{"id": "llm-model-default", "name": "deepseek-flash",
                            "model": "deepseek-flash"}],
            }],
        }}}, fh, ensure_ascii=False, indent=2)
    opencode_dir = os.path.dirname(connect.opencode_config_path(OPENCODE_HOME))
    os.makedirs(opencode_dir, exist_ok=True)
    with open(connect.opencode_config_path(OPENCODE_HOME), "w", encoding="utf-8") as fh:
        json.dump({"mcp": {connect.SERVER_NAME: {
            "type": "local", "command": [PY, os.path.join(_ROOT, "memory_agent", "proxy.py")],
            "enabled": True, "timeout": 20000}}}, fh, ensure_ascii=False, indent=2)


DEEPTUTOR_SCRIPT = r"""
import json
from deeptutor.services.embedding.config import get_embedding_config
from deeptutor.services.embedding.client import EmbeddingClient

cfg = get_embedding_config()
out = {
    "binding": cfg.binding,
    "provider_mode": cfg.provider_mode,
    "base_url": cfg.base_url,
    "model": cfg.model,
    "dim": cfg.dim,
}
vectors = EmbeddingClient(cfg).embed_sync(["alpha shared embedding", "beta second consumer"])
out["vector_dims"] = [len(v) for v in vectors]
print(json.dumps(out, ensure_ascii=False))
"""


def validate_with_deeptutor() -> dict | None:
    candidates = [os.environ.get("DEEPTUTOR_PYTHON"), shutil.which("python"),
                  r"D:\Users\Tan\AppData\Local\Programs\Python\Python312\python.exe"]
    python = next((c for c in candidates if c and os.path.isfile(c)
                   and subprocess.run([c, "-c", "import deeptutor"], capture_output=True)
                   .returncode == 0), None)
    if python is None:
        return None
    env = dict(os.environ)
    env["DEEPTUTOR_HOME"] = DT_HOME
    env.pop("PYTHONPATH", None)
    result = subprocess.run([python, "-c", DEEPTUTOR_SCRIPT], capture_output=True,
                            text=True, encoding="utf-8", env=env, cwd=WORK,
                            timeout=300)
    if result.returncode != 0:
        note(f"    DeepTutor 校验失败：{(result.stderr or '').strip()[-600:]}")
        return None
    return json.loads(result.stdout.strip().splitlines()[-1])


def main() -> int:
    global PORT, EMBEDDINGS_URL
    note(f"# issue #43 验收 @ {time.strftime('%Y-%m-%d %H:%M:%S')}")
    note(f"    workdir = {WORK}")
    PORT = _free_port()
    EMBEDDINGS_URL = connect.daemon_embeddings_url(HOST, PORT)
    note(f"    临时 daemon = http://{HOST}:{PORT}（独立端口，不动真 daemon）")
    setup_workspace()

    # --- 1. 写 DeepTutor 配置（mcp.json + model_catalog.json）----------------
    note("\n[1] connect.py 写 DeepTutor 配置（embedding profile + 保留 LLM）")
    rc = connect.run(deeptutor_home=DT_HOME, opencode_home=OPENCODE_HOME,
                     embeddings_url=EMBEDDINGS_URL, dry_run=False, ensure=False,
                     skill_source=os.path.join(_ROOT, "memory_agent", "skill"))
    check("一步安装（含 embedding profile）返回 0", rc == 0)

    catalog = connect.read_json_object(connect.deeptutor_model_catalog_path(DT_HOME))
    embedding = catalog["services"]["embedding"]
    check("embedding profile 已设为 active",
          embedding["active_profile_id"] == connect.EMBEDDING_PROFILE_ID,
          f"active={embedding['active_profile_id']}")
    check("保留 LLM profile 及其 key",
          catalog["services"]["llm"]["profiles"][0]["api_key"] == "sk-keep-me")

    before = open(connect.deeptutor_model_catalog_path(DT_HOME), "rb").read()
    _, changed_again = connect.merge_embedding_catalog(
        connect.read_json_object(connect.deeptutor_model_catalog_path(DT_HOME)),
        connect.build_embedding_profile(EMBEDDINGS_URL))
    after = open(connect.deeptutor_model_catalog_path(DT_HOME), "rb").read()
    check("幂等（再次合并 changed=False 且不改字节）",
          changed_again is False and before == after)

    # --- 2. 起临时 daemon（venv + 修好的 transformers） ---------------------
    note("\n[2] 启动临时 daemon（venv 路径；验证 transformers 回归已修）")
    proc = start_daemon()
    note(f"    daemon pid={proc.pid}")
    try:
        status, body = _post_embeddings({"input": "hello bge-m3 over daemon"})
        check("daemon 冷启动 + 首次 embeddings 请求成功", status == 200,
              f"status={status}")

        # --- 3. 端点契约 --------------------------------------------------
        note("\n[3] /v1/embeddings 契约")
        vector = body["data"][0]["embedding"]
        norm = sum(x * x for x in vector) ** 0.5
        check("OpenAI 形状 object=list/data[].embedding",
              body.get("object") == "list" and body["data"][0]["object"] == "embedding"
              and body["data"][0]["index"] == 0)
        check("dim=1024", len(vector) == 1024, f"dim={len(vector)}")
        check("单位范数（normalize_embeddings=True）", abs(norm - 1.0) < 1e-3,
              f"norm={norm:.4f}")

        status, batch = _post_embeddings(
            {"input": ["alpha one", "beta two", "gamma three"], "model": "BAAI/bge-m3"})
        check("批量输入返回等长向量",
              status == 200 and [len(d["embedding"]) for d in batch["data"]] == [1024] * 3
              and [d["index"] for d in batch["data"]] == [0, 1, 2])
        check("model 回显请求值", batch.get("model") == "BAAI/bge-m3")

        status, _ = _post_embeddings({"input": 123})
        check("非法 input → 400", status == 400, f"status={status}")
        status, _ = _post_embeddings({"input": "x", "encoding_format": "base64"})
        check("不支持的 encoding_format → 400", status == 400, f"status={status}")

        # --- 4. authn 同源 -------------------------------------------------
        note("\n[4] authn 与 /mcp 同源（零配置直连 + 无效 token 拒绝）")
        status, _ = _post_embeddings({"input": "ok"}, headers={"Authorization": "Bearer nope"})
        check("无效 Bearer → 401", status == 401, f"status={status}")
        status, _ = _post_embeddings({"input": "ok"})
        check("无 token 配置 → 零配置直连", status == 200, f"status={status}")

        # --- 5. DeepTutor 自身端到端 --------------------------------------
        note("\n[5] 用 DeepTutor 自身 EmbeddingClient 端到端取向量")
        dt = validate_with_deeptutor()
        if dt is None:
            check("DeepTutor 端到端取向量", False, "未找到可 import deeptutor 的 Python")
        else:
            check("DeepTutor 解析为 local vllm + bge-m3（dim=1024）",
                  dt["binding"] == "vllm" and dt["provider_mode"] == "local"
                  and dt["model"] == "BAAI/bge-m3" and dt["dim"] == 1024,
                  f"binding={dt['binding']} mode={dt['provider_mode']} dim={dt['dim']}")
            check("DeepTutor 真的取到 2×1024 向量",
                  dt["vector_dims"] == [1024, 1024], str(dt["vector_dims"]))
            check("base_url 指向共享 daemon 的 /v1/embeddings",
                  dt["base_url"].rstrip("/").endswith("/v1/embeddings"), dt["base_url"])
    finally:
        _kill_tree(proc.pid)
        time.sleep(1.0)

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    total = len(CHECKS)
    verdict = "PASS" if passed == total else "FAIL"
    note(f"\n结论：{verdict}  ({passed}/{total})")
    write_results(verdict, passed, total)
    return 0 if passed == total else 1


def write_results(verdict: str, passed: int, total: int) -> None:
    lines = [
        "# #43 验收结果：共享 daemon 暴露 BGE-M3 embeddings 端点",
        "",
        f"- 日期：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        "- 脚本：`memory_agent/eval/bge_m3_embeddings_43.py`",
        f"- 结论：**{verdict}**（{passed}/{total}）",
        f"- 临时工作区：`{WORK}`（临时 KB / 索引 / DeepTutor home + 独立端口；真 daemon 未动）",
        "",
        "## PASS 矩阵",
        "",
        "| # | 检查 | 结果 | 详情 |",
        "|---|------|------|------|",
    ]
    for index, (name, ok, detail) in enumerate(CHECKS, start=1):
        lines.append(f"| {index} | {name} | {'PASS' if ok else 'FAIL'} | "
                     f"{str(detail).replace('|', chr(92) + '|')} |")
    lines += ["", "## 运行日志", "", "```text", *RESULTS, "```", ""]
    with open(RESULTS_FILE, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    print(f"[结果] 已写入 {RESULTS_FILE}")


if __name__ == "__main__":
    raise SystemExit(main())
