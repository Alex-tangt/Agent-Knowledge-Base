# 支撑证据 · 本地 GGUF 嵌入并发崩溃（阻断 M5.4 clean-room 恢复）

- 结论：**PASS**（并发是被确认的触发条件，其余解释被逐一排除）
- 生成时间：2026-10-10T15:21:10+0800
- 模型：`bge-small-zh-v1.5-f16.gguf`（llama-cpp-python 0.3.36，CPU）

| 探针 | 目的 | 退出码 | 是否崩溃 | 断言 |
|---|---|---|---|---|
| `python probe_local_embedder.py normal` | 单个短中文输入 | 0 | False | `` |
| `python probe_local_embedder.py empty` | 空字符串 + 单字符输入（排除『空输入致崩』） | 0 | False | `` |
| `python probe_local_embedder.py long` | 56000 字符超长输入（排除『超上下文致崩』） | 0 | False | `` |
| `python probe_local_embedder_batch.py 2` | 批量 2 条（排除『批处理致崩』） | 0 | False | `` |
| `python probe_local_embedder_batch.py 32` | 批量 32 条 | 0 | False | `` |
| `python probe_local_embedder_concurrency.py serial 10` | 10 线程 + 显式加锁串行访问同一 Llama 上下文 | 0 | False | `` |
| `python probe_local_embedder_concurrency.py concurrent 10` | 10 线程**并发**访问同一 Llama 上下文（模拟候选默认 max_concurrent=10） | 3221226505 | True | `D:\a\llama-cpp-python\llama-cpp-python\vendor\llama.cpp\ggml\src\ggml-backend.cpp:338: GGM` |

## 结论

并发访问同一个 Llama 上下文不可用：单输入（空/超长）、批量输入、以及加锁串行访问都正常，只有并发访问触发原生 GGML_ASSERT 并使进程整体退出。候选默认 embedding.max_concurrent=10；实测把 clean-room 配置改成 1 仍无法让整账户/范围包的恢复走完（服务在恢复过程中被 abort）。

## 对 M5 的影响

所以 M5.4（干净位置恢复）在本机判 FAIL：不是恢复语义错，而是本地嵌入路径让恢复过程无法完成。

## 建议下一步

在框架侧给本地嵌入调用加互斥（或在候选内串行化 embedding worker）后重测；或改用进程外/HTTP 的嵌入提供方。
