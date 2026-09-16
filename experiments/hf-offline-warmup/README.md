# 实验：冷启动的对外 HF 请求（#18）

**问题**：`local_files_only=True` 已设置，但冷启动仍向 huggingface.co 发请求；在弱网 / 代理不稳
（本机经 VPN）环境下会把模型加载拖成分钟级卡顿甚至直接失败。

**假设**：`sentence_transformers` / `transformers` 在解析模型 **revision / 元数据** 时仍走
`huggingface_hub` 的 model-info API，`local_files_only` 只挡「文件下载」、不挡这条元数据解析路径；
`HF_HUB_OFFLINE=1` 可整条短路。

## 设置

- 脚本：`probe_hf_offline.py`（驱动，干净子进程逐变体跑）+ `load_probe.py`（单次加载 + 计时 + 打 HF/httpx 日志）。
- 变体（环境注入，不读项目配置）：
  - `baseline`：无额外 env（真实网络）
  - `unreachable`：`HF_ENDPOINT=http://10.255.255.1`（模拟代理挂 / 弱网；短超时 90s）
  - `unreachable+off`：`HF_ENDPOINT` + `HF_HUB_OFFLINE=1`
  - `offline`：`HF_HUB_OFFLINE=1`
- 代理环境变量在子进程里清空，保证「不可达」判定确定。
- 模型：`BAAI/bge-m3`（embed）、`BAAI/bge-reranker-v2-m3`（rerank），均**已在本地缓存**。
- 机器版本：huggingface_hub 0.36.2 / sentence-transformers 5.4.1 / httpx 0.28.1。

## 数据（`probe_results.json`，机器产出）

| 目标 | 变体 | 对外请求数 | 结果 | 加载耗时 |
|---|---|---|---|---|
| embed | baseline | **6** | ok | 29.0s |
| embed | unreachable | 2 | **失败**（`ValueError: Unrecognized processing class`） | 33.0s |
| embed | unreachable+off | **0** | ok | 27.8s |
| embed | offline | **0** | ok | 27.4s |
| rerank | baseline | **1** | ok | 29.4s |
| rerank | unreachable | 1 | **失败**（同上 `ValueError`） | 34.1s |
| rerank | unreachable+off | **0** | ok | 27.2s |
| rerank | offline | **0** | ok | 27.1s |

**baseline(embed) 的 6 次请求**（正是 issue #18 记录的现象，且抓到了调用者）：

```
GET  https://huggingface.co/api/models/BAAI/bge-m3                   200
GET  https://huggingface.co/api/models/BAAI/bge-m3                   200
GET  https://huggingface.co/api/models/BAAI/bge-m3/commits/main      200
GET  https://huggingface.co/api/models/BAAI/bge-m3/discussions?p=0   200
GET  https://huggingface.co/api/models/BAAI/bge-m3/commits/refs%2Fpr%2F130  200
HEAD https://huggingface.co/BAAI/bge-m3/resolve/refs%2Fpr%2F130/model.safetensors.index.json  404
```

即：本地缓存快照带一个 PR ref（`refs/pr/130`），`sentence_transformers` 为解析 revision 去查
model-info / commits / discussions——**这条路径不受 `local_files_only` 约束**。rerank 侧对应
1 次 `GET /api/models/BAAI/bge-reranker-v2-m3`。

**更早一次长超时运行**（`unreachable` 超时设 240s）：embed 与 rerank 均 **>240s 未完成加载**
——与 issue 描述「VPN 不稳时 warmup 被拖到分钟级」一致。弱网下失败形态取决于超时/错误时序：
要么长时间挂起，要么直接 `ValueError` 失败（不是 graceful 降级）。

## 结论

1. **根因确认**：`local_files_only=True` **不覆盖** HF 元数据/revision 解析；两个模型各会发此请求，
   弱网下表现为 >240s 挂起或直接加载失败。issue 里的 `refs/pr/130` HEAD 已复现并定位到 bge-m3 的
   缓存 PR-ref 解析。
2. **修复手段已验证**：`HF_HUB_OFFLINE=1` 让对外请求数归零、加载稳定在 ~27–29s（两模型一致）。
3. **代价**：`HF_HUB_OFFLINE=1` 是进程级全局开关；**首次下载**（缓存缺失）会被它挡住，需在需要下载时
   显式关掉（`HF_HUB_OFFLINE=0`），或按「模型已缓存」再开。落地方式见 issue #18 / 决策记录。
4. `experiments/rerank-latency/bench_rerank.py` 早已用 `os.environ.setdefault("HF_HUB_OFFLINE","1")`
   规避（先例），但**产品入口**没有——这正是本 issue 的可复现缺口。

## 修复与验收（缓存感知自动离线）

**改动**：新增 `ragcore/config/hf.py::ensure_hf_offline(model_names)`——在 import HF 之前，若目标模型
已全部缓存则设 `HF_HUB_OFFLINE=1`+`TRANSFORMERS_OFFLINE=1`；有缺失则保持联网（首次下载仍可用）并告警；
显式设置的环境变量（含 `0`）不覆盖。`local_embedding_service` / `reranker_service` 各自在 import
`sentence_transformers` 之前按自己的模型调用它。

**服务层验收**（`probe_results.json` 的 `svc_embed` / `svc_rerank`，不可达 endpoint 下）：

| 目标 | 变体 | 修复前 | 修复后 |
|---|---|---|---|
| svc_embed | baseline | 6 请求 | **0 请求**，27.3s ok |
| svc_embed | unreachable | 挂起 / 失败 | **0 请求**，27.5s ok |
| svc_rerank | baseline | 1 请求 | **0 请求**，26.9s ok |
| svc_rerank | unreachable | 挂起 / 失败 | **0 请求**，26.7s ok |

即：走产品服务层时，**不可达网络下的加载失败/挂起消失**，且不再产生任何对外 HF 请求。

单测 `tests/unit/test_hf_offline.py`（8 项）覆盖缓存判定 / 缺失保持联网 / 显式开关不被覆盖。

## #46 追加：运行时兜底（HF 已被依赖链先 import）

**问题**：#18 的修复只在「import HF 之前」设 `os.environ`。但 `huggingface_hub.constants.HF_HUB_OFFLINE`
是 **import 期常量**；`memory_agent` 的依赖链（qdrant_client → huggingface_hub）会先把 HF import 进来，
于是这次调用变成**空操作**——新进程里常量仍是 `False`（架构层三角测量，见 issue #46）。

**修复**：`ensure_hf_offline()` 设完环境变量后，按**实际 import 图**改写已加载模块里的常量副本
（`_patch_loaded_hf_modules()`）：`huggingface_hub.constants.HF_HUB_OFFLINE`（源头）、
`transformers.utils.hub._is_offline_mode`（transformers 的 import 期缓存，`is_offline_mode()` 只读它）、
`transformers.commands.serving.HF_HUB_OFFLINE`（唯一的 `from ... import` 绑定）。**先枚举实际绑定**
（只改 `constants` 会漏 `transformers` 缓存）。入口（`memory_agent/runtime.py` 经 `_bootstrap.configure_hf_offline()`、
`retrieval_eval.py` / `build_eval_set.py`）另在 import 任何 HF 之前先调一次。

**A/B 证据**（`probe_46_runtime_fallback.py`，不可达 `HF_ENDPOINT=http://10.255.255.1`、无 shell env、
先 `import qdrant_client` 制造「HF 已被先 import」条件）：

| 变体 | 结果 | 加载耗时 | 外呼次数 | `HF_HUB_OFFLINE` |
|---|---|---|---|---|
| `fixed`（带运行时兜底） | **ok** | **20.7s** | **0** | `True` |
| `legacy`（兜底打成 no-op，模拟修复前） | **失败** `ValueError` | **253.4s** | **31** | `False` |

新进程断言（主树 venv，工作树 `PYTHONPATH`）：
```
$ import memory_agent.runtime; import huggingface_hub.constants as c; c.HF_HUB_OFFLINE
True   # 日志：HF hub offline mode enabled (all models cached): BAAI/bge-m3, ...
```
若 HF 确实已先被 import，日志会补 `; patched already-imported: huggingface_hub.constants.HF_HUB_OFFLINE`。

**回归**：`tests/unit/test_hf_offline.py` 增至 12 项（新增「HF 已先 import 仍生效」「缺失保持常量在线」
「显式 `0` 不翻转已加载常量」「按 import 图改写缓存副本」）；`pytest tests/unit -q` → **385 passed /
2 skipped / 3 xfailed**。

