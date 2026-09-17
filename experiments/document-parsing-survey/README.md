# 文档解析选型调研（收录管线）

- 日期：2026-09-17
- 目的：为「统一索引的文档解析」选型——确定**真相源模型**与**首装引擎**
- 方式：**一手来源**（LightRAG / Docling / MinerU / MarkItDown 仓库 README 与许可文件）+ 本地依赖核查

## 问题

统一索引是**条目级**（1 条 = 1 个索引点），语料只收 `.md`（`memory_agent/corpus/loader.py::_iter_markdown`）。
要支持 PDF / DOCX 等，需要「**文档 → 结构化条目**」的解析 / 收录管线。选什么真相源模型、什么引擎？

## 一手事实

### LightRAG 的解析体系（可借模式）

- `LIGHTRAG_PARSER=*:native-iteP,*:mineru-iteP,*:legacy-R` —— **glob → 引擎 + 分块策略**的可插拔路由。
- 引擎：**native / MinerU / Docling / legacy**，可扩展第三方；多模态（图 / 表 / 公式）经 RAG-Anything 并入，可选 VLM 处理。
- 分块策略 4 种：`Fixed` / `Recursive` / `Vector` / **`Paragraph` 语义**；docx **Smart Heading**（spaCy 模型）。
- 许可：**MIT**。

### 候选引擎（已核实）

| 引擎 | 许可 | 格式 | PDF 质量 | 体积 | 输出 |
|---|---|---|---|---|---|
| `pypdf`（**已装**） | BSD | PDF | 纯文本，无版式 / 表格 | 零新增 | text |
| **MarkItDown**（Microsoft） | MIT | PDF / Office / HTML / EPUB / 图像 … | 低–中（**官方自认非高保真**） | 轻 | md |
| **Docling**（IBM / LF AI & Data） | **MIT** | PDF / DOCX / PPTX / XLSX / HTML / EPUB / 图像 … | **高**（版式 / 阅读序 / 表格 / 公式 / OCR / VLM） | 中（模型） | **md + 无损 JSON** |
| **MinerU 4.0** | **Apache-2.0 + 附加条款** | PDF / Office / RTF / EPUB / OFD / HTML / CSV | 最高（4 tier、公式、页 / 块 locator） | 重（模型 0.8–3GB + 后台服务） | md / JSON / … |

- **MinerU 附加条款**：商用门槛（MAU > 1 亿 或 月收入 > $2000 万需单独商用许可）；**在线服务须显著标注**。
- **Docling**：本地执行（air-gapped 可用）；`pip install docling`，Python ≥ 3.10；`DocumentConverter().convert(src).document.export_to_markdown()`；有 MCP server / docling-serve。
- **MarkItDown**：`pip install 'markitdown[all]'`，Python ≥ 3.10；文档明示"面向文本分析管线，非高保真"；安全注意（以进程权限做 I/O，勿传不可信输入）。
- **另有** `unstructured==0.22.21` 已钉在根 `requirements.txt` 但 **venv 未安装**（来源不明）——需澄清留用还是删除。

## 本项目实证（决定设计的约束）

Phase A（#47）实测：MultiHop-RAG 609 篇正文 **477 篇 > `MAX_ENTRY_CHARS=6000`**；
送嵌窗口**外**的 gold 槽 `recall@50 0.936 / @5 0.434`，窗口**内**为 `0.979 / 0.708`。
→ **一份长文档塞一个条目会丢证据**（约 40% fact 落在窗口外）。

## 结论

1. **定位**：这是「文档 → 结构化 **Markdown 条目**」的**收录管线**，解析引擎只是其中一环。
2. **真相源模型**：解析一次 → **物化 Markdown、按节切分**（每节一个 `.md` + frontmatter）→
   复用现有 `.md` 条目管线。ADR-0025 D1（基表 = 版本化纯文本 + git）**不变**，且顺带修掉 6000 字截断。
3. **引擎**：可插拔 `DocumentParser` 端口 + **Docling 首装 + `pypdf` 兜底**；MinerU / MarkItDown 留作后续可插。
4. **打包**：重依赖走 **optional extra**，**不进程内 daemon**。
5. **范围**：**本地个人模式**；入口 = **全局 KB 上传为主**；格式先 **PDF + DOCX**。

决策落点：`docs/adr/0027`。

## 来源

- LightRAG：`github.com/HKUDS/LightRAG`（README；MIT）
- Docling：`github.com/docling-project/docling`（README；MIT；arXiv 2408.09869）
- MinerU：`github.com/opendatalab/MinerU`（README + `LICENSE.md`；Apache-2.0 + 附加条款）
- MarkItDown：`github.com/microsoft/markitdown`（README）
- 本地：`requirements.txt` / venv 依赖核查；`memory_agent/corpus/loader.py`
