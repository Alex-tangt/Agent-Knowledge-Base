# 0027 文档解析与收录：物化 Markdown·按节切 + 可插拔引擎

Status: **accepted**（2026-09-17 owner 拍板）。

Relates: **ADR-0025**（基表 = 文件 + git；D8 收录机制；D19 写侧单目标）、**ADR-0019**（端口 + 平面分工的范式）、
**ADR-0024**（打包 / optional extra 先例）、**#36**（overlay / 收录）、**#47**（6000 字截断实证）；
调研见 `experiments/document-parsing-survey/`。

## 背景

统一索引是**条目级**且只收 `.md`（`memory_agent/corpus/loader.py::_iter_markdown`）。要支持 PDF / DOCX 等，
缺的是「**文档 → 结构化条目**」的收录管线，而不仅是"接一个 parser"：

- LightRAG 的解析体系是**可路由的多引擎管线**（`LIGHTRAG_PARSER=*:native-iteP,*:mineru-iteP,…`：
  glob → 引擎 + 分块策略；引擎 native / MinerU / Docling；4 种分块含 Paragraph 语义）——
  值得借的是**端口 + 标题感知分块**这层形状。
- 本项目实证约束（#47）：长文档塞一个条目会**丢证据**——MultiHop-RAG 609 篇里 **477 篇 > `MAX_ENTRY_CHARS=6000`**，
  送嵌窗口外的 gold 槽 `recall@50` 0.979 → 0.936。

## 决策

- **D1 定位 = 收录管线，不是"接 parser"**：交付物是「文档 → 结构化 **Markdown 条目**」的管线；
  解析引擎只是其中一环。
- **D2 真相源模型 = 物化 Markdown · 按节切**：解析一次 → **每节一个 `.md`（带 frontmatter）** →
  复用**现有** `.md` 条目管线。**ADR-0025 D1 不变**（基表仍是版本化纯文本 + git）；
  只读语料仍按 D8 走注册表 / overlay。
- **D3 引擎 = 可插拔 `DocumentParser` 端口**：首装 **Docling**（MIT、本地 / CPU、高保真 PDF、直出 Markdown），
  **`pypdf` 兜底**（已有依赖）；MinerU / MarkItDown 留作后续可插。
  **v1 不做** glob → 引擎路由（固定单引擎 + 扩展点），避免过早引入 LightRAG 式配置面。
- **D4 按节切分即条目切分**：节的粒度对齐 entry 粒度，**刻意把每节控制在嵌入窗口内**——
  直接消掉 #47 的 6000 字截断损失。
- **D5 重依赖不进 daemon**：解析依赖走 **optional extra `memory-agent[parse]`**（学 `[bm25]`，
  `pyproject.toml` 不压默认包体）；解析是**写入前置步骤 / 独立进程**，daemon 保持确定性、不加载解析模型。
- **D6 范围**：**本地个人模式**；入口 = **全局 KB 上传为主**；格式先 **PDF + DOCX**；
  **不做**共享 / 云（冻结区）、不改检索默认 / 合成、v1 不做多模态（图 / 表转文本）。

## 理由

- **保 ADR-0025 不变形**：物化 Markdown 让"文件 + git 即基表"继续成立，索引 / 检索 / `owner` / `status`
  全复用，**零检索改动**。
- **复用开源基线优先**：Docling 是 MIT、本地、直出 Markdown，与"基表 = 纯文本"同构；
  MinerU 质量更高但更重且带在线服务标注义务（见调研页）。
- **端口化**（同 `VectorStore` 端口先例）让换引擎 / 加格式不触发重写。

Considered options：

- **A 物化 Markdown · 按节切（采用）**——入现有管线、可 git、修截断；代价是产出更多文件。
- **B 物化 Markdown · 每文档一份（弃）**——少文件，但长文档仍撞 6000 字窗口，需另做 chunk 条目。
- **C 不物化 · 索引期内存解析（弃）**——二进制成真相源，需 **amend ADR-0025**，且重解析成本高、不可 git diff。
- 引擎：**Docling 首装（采用）** / MarkItDown（轻但官方自认非高保真）/ MinerU（最高质但重 + 标注义务）/
  仅 `pypdf`（零依赖但无版式表格）。

## Consequences

- **新可选依赖** `memory-agent[parse]`（Docling）；基础包与 daemon 不变重。
- **新增证据目录** `experiments/document-parsing-survey/`（本调研）。
- **解析质量成为新的质量杠杆**：需评测"解析后条目 vs 原文窗口"的检索对照（沿用 #24 harness 口径）。
- **旧钉依赖待澄清**：根 `requirements.txt` 的 `unstructured==0.22.21` 已钉未装、来源不明——留用还是删除？
- **两个未决（下一票定）**：① **原始二进制**是否入库（gitignored 附件 vs 提交）；② **入口形态**
  （新 MCP 工具 vs CLI / 管理命令）。
- 与 ADR-0025（D8 / D19）、ADR-0024（打包）一致；本 ADR 不修改它们。
