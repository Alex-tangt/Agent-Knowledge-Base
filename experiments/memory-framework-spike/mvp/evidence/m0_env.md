# M0 · 能部署并启动（隔离实例）

- 结论：**PASS**（服务真实就绪：`/health.healthy=true` 且 `/ready.status=ready`）
- 生成时间：2026-10-10T14:22:52+0800
- 端口/地址：http://127.0.0.1:8781（auth_mode=dev）
- 数据工作区：`C:\Users\Tan\.openviking-mvp-spike\data`（仓库外）
- 解译器：Python 3.12.2
- 分发版本：`openviking==0.4.23`（License AGPL-3.0）；`ov` 二进制自报 `CLI:     0.4.24.dev0`（预编译二进制，版本线与 Python 包不同）
- 安装体积：1102342141 bytes / 104294 files / 506 顶层条目（site-packages）
- 启动命令：`D:\python_work\work2026-4\Agent-Knowledge-Base\venv-ov\Scripts\openviking-server.exe --config C:\Users\Tan\.openviking-mvp-spike\ov.conf --host 127.0.0.1 --port 8781`
- 启动方式：`python spike_server.py start`（内部以 `subprocess.Popen` 起 `openviking-server.exe`，stdout/stderr 落 `C:\Users\Tan\.openviking-mvp-spike\logs`）

## 探测结果

| 探测 | 结果 |
|---|---|
| `GET /health` | `{"status": "ok", "healthy": true, "version": "0.4.23", "auth_mode": "dev"}` |
| `GET /ready` | `{"status": "ready", "checks": {"agfs": {"status": "ok", "checks": {"filesystem": "ok", "multiwrite_sync": "not_supported"}}, "vectordb": "ok", "api_key_manager": "not_configured", "embedding": "ok", "ollama": "not_configured"}}` |
| `GET /api/v1/system/status` | `{"status": "ok", "result": {"initialized": true, "user": "default"}, "error": null, "telemetry": null, "profile": null}` |

## 配置键（无凭据值）

- 服务端配置：`C:\Users\Tan\.openviking-mvp-spike\ov.conf`（无 BOM：True，sha256 `ceef226af458eafeeb2e263c0bba97e935c50560d94cbd61c5f39acc2a7fbda8`）
- 客户端配置：`C:\Users\Tan\.openviking-mvp-spike\ovcli.conf`
- 环境变量键：`OPENVIKING_CONFIG_FILE` / `OPENVIKING_CLI_CONFIG_FILE`

```json
{
  "default_account": "default",
  "default_user": "default",
  "storage": {
    "workspace": "C:\\Users\\Tan\\.openviking-mvp-spike\\data"
  },
  "embedding": {
    "dense": {
      "provider": "local",
      "model": "bge-small-zh-v1.5-f16"
    }
  },
  "server": {
    "host": "127.0.0.1",
    "port": 8781
  }
}
```

## 隔离证明

- 状态目录位于仓库外：True
- 本实例不启用任何云模型、进程内移除 `HTTP(S)_PROXY`、`NO_PROXY` 去掉带方括号的 IPv6
- 未触碰真实 KB / 真实索引 / 8765 daemon / `~/.openviking`

## 复现

```powershell
. ./spike-env.ps1                 # 设 OPENVIKING_CONFIG_FILE / OPENVIKING_CLI_CONFIG_FILE / 端口 8781
python spike_server.py start      # 首次冷启动约 45 s（加载本地 GGUF + 建集合）
python m0_inventory.py            # 生成本证据
```
