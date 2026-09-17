# #51 上传接线验收证据（只读语料域）

- 日期：2026-09-17
- 票据：**#51**（全局 KB 上传接线）+ 规格评论；ADR-0027 D2/D7/D8/D9、ADR-0025 D8。
- 套件：`memory_agent/eval/ingest_51.py`（可复跑；**不加载任何模型**——Stub 嵌入 + Qdrant local，秒级）。
- **方向修正（owner 2026-09-17 当场定）**：解析出的文档属**只读语料 / 向量库域**，
  **不写记忆 KB**——故本票**不用 `MemoryWriter`、不 git commit**，物化 `.md` 经 **overlay 显式收录**
  为只读来源（`writable=false`）。这条与 #51 票面「写全局 KB + 一次操作一个 commit」不同，
  ADR-0027 D6/D8 与 #51 验收措辞**待架构层 amend**（本执行会话不改 ADR / 票面）。

## 环境（D7：解析只装进专用解析环境）

- 主 venv：**未装 Docling**（套件断言 `find_spec("docling") is None`）；`transformers==4.57.6` 等钉不动。
- 专用解析环境：`venv-parse\`（gitignored；`python -m venv venv-parse`）装
  `docling==2.128.0` + `pypdf` + `python-dotenv`，实测 `torch==2.14.0+cpu`。
- 解析由主环境经**子进程**调用：`python -m memory_agent.parse_worker`（D7）。

## 复现命令与结果

```powershell
# 1) 主环境 pypdf 兜底路径（端到端：物化 + overlay 收录 + 惰性刷新召回）
venv\Scripts\python.exe memory_agent/eval/ingest_51.py
#    → 12/12 通过

# 2) 真 Docling 路径（--parse-python 指向专用解析环境）
venv\Scripts\python.exe memory_agent/eval/ingest_51.py --parse-python .\venv-parse\Scripts\python.exe
#    → 14/14 通过（多出 6a/6b：真 Docling 解析 DOCX 且按标题切节 + 条目可召回）

# 3) 单测（接线：原件暂存 / 物化 / 收录 / CLI / 错误路径）
venv\Scripts\python.exe -m pytest tests/unit -q
#    → 426 passed, 3 skipped, 3 xfailed
```

真 Docling 直跑（专用环境）样例：

```powershell
venv-parse\Scripts\python.exe -m memory_agent.parse_worker --file sample.docx
# {"engine":"docling","sections":[{"title":"Quarterly Overview","level":2,...},{"title":"Cost Details","level":3,...}]}
venv-parse\Scripts\python.exe -m memory_agent.parse_worker --file sample.pdf
# {"engine":"docling","sections":[{"title":"sample","level":1,...}]}
```

## 通过矩阵（14/14，`--parse-python` 版）

| # | 断言 | 结果 |
|---|---|---|
| 0 | 主 venv 未装 Docling（D7） | PASS |
| 1a | 上传前 import 来源不存在 | PASS |
| 2a | 物化出条目并落只读 import 根 | PASS |
| 2b | 原件存 gitignored 上传目录（D9，逐字一致） | PASS |
| 2c | overlay 写入 include（显式收录） | PASS |
| 3a | `writable=false` / `owner=label` / `source=label/rel` | PASS |
| 3b | frontmatter 合规；`id == source` 去 `.md` | PASS |
| 3c | 每节 ≤ `MAX_ENTRY_CHARS` | PASS |
| 4a | selection 把 import 视为 `explicit` 只读来源 | PASS |
| 5a | 上传后 `memory_search` 惰性刷新即召回（D9，免重启） | PASS |
| 5b | 命中的只读语义正确（`writable=false` / `owner=label`） | PASS |
| 6a | 真 Docling 在独立环境解析 DOCX 且保留标题切节 | PASS |
| 6b | 真 Docling 解析的 DOCX 条目可召回 | PASS |
| 7 | 真实工作树前后不变（隔离） | PASS |

## 事实与边界

- **读取语义**：只读条目 `source = "<label>/<rel>"`、`owner = <label>`、`id = source 去 .md`；
  `status=current`、`type=research`、`tags=[imported]`（CLI 可覆盖）。
- **原件 / 物化件**：均在 **gitignored** 目录（`memory_agent/uploads/`、`memory_agent/imports/`）；
  只读域**无 git 版本管理**（真相源仍是物化 Markdown）。
- **隔离**：套件全程在临时沙箱（KB / registry / overlay / index / imports / uploads 皆临时）；
  真实工作树 `git status --porcelain` 与 `HEAD` 前后逐字不变。
- **Docling 夹具注意**：手搓的最小 DOCX 若**缺 `word/styles.xml`**，Docling 不认 `pStyle Heading1`
  → 输出无 `#` 标题、切节退化为单节；补 `styles.xml`（本套件 `_make_docx` 已含）即输出
  `##/###` 并正确按标题切节。PDF 无标题结构时同样单节（符合预期）。
- **离网 / air-gapped**：Docling 首次 `convert` 会下载模型到 HF 缓存；部署需先在一次联网机上
  预热缓存（跑一次解析）再携带缓存目录（`HF_HOME` 可指定路径）——已写入 `memory_agent/README.md`。

## 不受影响

- 不改检索默认 / 合成、`SKILL.md`、`corpus/loader.py`、daemon；只读语料既有行为不变。
- 未新增硬依赖：解析引擎仍走 `memory-agent[parse]`（`#50`），主包体不变重。
