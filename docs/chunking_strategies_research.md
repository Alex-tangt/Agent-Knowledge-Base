# RAG 文档切片策略与评估标准调研报告

> 调研时间：2026-07  
> 调研来源：GitHub 仓库、官方文档、开源工具

---

## TL;DR 摘要

1. **切片策略**：主流框架（LangChain / LlamaIndex / Haystack）普遍采用分层策略——先按文档类型识别结构边界（如法律条文的"第X条"），再使用递归字符分割兜底。语义切片（基于 embedding 相似度变化检测话题边界）是近两年最重要的进展，但计算成本高。
2. **评估标准**：RAGAS 和 DeepEval 是目前最成熟的 RAG 评估框架，但它们衡量的是**检索质量**（Context Recall / Precision），而非**单个 chunk 的固有质量**。Chunk 级别的语义完整性、自包含性、边界质量目前缺少标准化度量。
3. **对本项目的参考价值**：项目当前的"第X条"法律条文识别 + RecursiveCharacterTextSplitter 兜底策略与业界最佳实践一致。可优化的方向：引入 chunk 元数据（标题、层级）、上下文窗口增强（small-to-big 检索）、以及用 RAGAS 做系统性质量回归。

---

## 一、切片策略

### 1.1 主流框架策略对比

| 策略 | LangChain | LlamaIndex | Haystack | 本项目 |
|------|-----------|------------|----------|--------|
| **固定大小 (字符/Token)** | `CharacterTextSplitter` | `TokenTextSplitter` | `DocumentSplitter` | — |
| **递归字符分割** | `RecursiveCharacterTextSplitter` | `SentenceSplitter` | 内建支持 | 兜底方案 |
| **语义切片** | 第三方（如 semchunk） | `SemanticSplitterNodeParser` | — | — |
| **文档类型感知** | 语言特定 separators | 多解析器（PDF/HTML/Code） | Docling 集成 | "第X条" 识别 |
| **Markdown 标题感知** | `MarkdownHeaderTextSplitter` | — | — | `_extract_title()` |
| **层次化（small-to-big）** | ParentDocumentRetriever | `AutoMergingRetriever` | Parent Document Retriever | — |
| **Late Chunking** | — | chonkie 库 | — | — |

### 1.2 LangChain 切片体系

**源码位置**：[`langchain-ai/langchain`](https://github.com/langchain-ai/langchain) → `libs/text-splitters/langchain_text_splitters/`

#### RecursiveCharacterTextSplitter（递归字符分割）

- 优先级分隔符序列：`["\n\n", "\n", " ", ""]`
- 从高优先级分隔符开始尝试，直到找到能使 chunk 不超过 `chunk_size` 的分隔符
- 若在某个分隔符级别下仍有超长片段，递归降级到更低优先级分割
- 支持 `chunk_overlap` 重叠保持上下文连续性

```python
# 核心源码：langchain_text_splitters/character.py
class RecursiveCharacterTextSplitter(TextSplitter):
    def __init__(self, separators=None, ...):
        self._separators = separators or ["\n\n", "\n", " ", ""]
    
    def _split_text(self, text, separators):
        # 递归尝试不同分隔符，优先在高层次边界切割
```

#### 语言感知分割

支持 30+ 编程/标记语言的特殊分隔符，例如：
- **Python**：`\nclass `, `\ndef `, `\n\tdef ` → `\n\n` → `\n` → ` ` → `""`
- **Markdown**：`\n#{1,6} `, `` ```\n ``, 水平线 → `\n\n` → `\n` → ` `
- **LaTeX**：`\chapter{`, `\section{`, `\subsection{`

**源代码**：`langchain_text_splitters/character.py:260-650`（`get_separators_for_language` 方法）

#### MarkdownHeaderTextSplitter

按 Markdown 标题层级分割，保留标题作为每个 chunk 的元数据：
- 标题层级作为 metadata 注入 chunk
- 支持标题追溯（chunk 知道它属于哪个 H1 → H2 → H3 路径）

> **对本项目的启示**：如果政策法规文档有标题层级结构，可使用此策略替代当前简单的 `_extract_title()`。

### 1.3 LlamaIndex 切片体系

**文档**：https://docs.llamaindex.ai/en/stable/module_guides/loading/node_parsers/

#### SentenceSplitter（句子分割器，默认选择）

- 按句子边界切分（尊重标点符号）
- `chunk_size=1024`（字符），`chunk_overlap=20`
- 适用于大多数通用场景

#### TokenTextSplitter（Token 分割器）

- 使用 tokenizer 按 token 数精确控制 chunk 大小
- 适合需要精确控制 LLM 上下文窗口的场景

#### SemanticSplitterNodeParser（语义分割器）

- 核心思想：计算相邻句子的 embedding 余弦相似度，相似度急剧下降处即为"话题边界"
- 使用 breakpoint percentile threshold 控制切分粒度
- 优点：chunk 语义连贯，不会在话题中间截断
- 缺点：索引时需要额外 embedding 计算，比普通分割慢 10-100 倍

```python
from llama_index.core.node_parser import SemanticSplitterNodeParser

splitter = SemanticSplitterNodeParser(
    buffer_size=1,         # 比较窗口
    breakpoint_percentile_threshold=95,  # 相似度阈值
    embed_model=embed_model
)
```

#### Hierarchical / Auto-Merging Retriever

- 索引小 chunk（如 256 tokens）用于精确匹配
- 检索时返回大 chunk（如 1024 tokens，包含完整段落/章节）给 LLM
- 在 chunk 间维护父子关系映射

> **来源**：LlamaIndex 官方文档 - Node Parser Usage Pattern / Node Parser Modules

### 1.4 Haystack 切片体系

**源码**：[`deepset-ai/haystack`](https://github.com/deepset-ai/haystack)

- `DocumentSplitter`：支持 `split_by="word"` / `"sentence"` / `"passage"` / `"page"`
- 内置 `split_length` 和 `split_overlap` 参数
- 通过 Docling 集成实现 PDF/DOCX 的文档类型感知解析
- 支持 Parent Document Retrieval 模式

> **来源**：redhat-et/PRAGmatic → `pragmatic/haystack/docling_splitter.py`

### 1.5 切片粒度控制策略

| 粒度 | 适用场景 | 典型参数 |
|------|---------|---------|
| **Token 级** | 精确控制 LLM 输入长度 | 256-512 tokens（GPT），使用 tiktoken 计数 |
| **字符级** | 通用中文/多语言文本 | 500-1000 字符 |
| **句子级** | 需要语义完整性的 QA | 3-8 句 |
| **语义级** | 话题边界敏感的知识问答 | 自适应长度 |

**关键发现**：中文法律文档的特殊性在于"第X条"本身就是天然的语义边界。本项目的 `_split_by_articles()` 策略直接利用了这一特征，是对"文档类型感知切片"的精准实现。

### 1.6 上下文窗口保持技术

| 技术 | 机制 | 优点 | 缺点 |
|------|------|------|------|
| **chunk_overlap** | 相邻 chunk 共享 N 个字符 | 简单、通用 | 增加存储冗余（~15-20%） |
| **Sliding Window** | 以固定步长滑动窗口 | 不遗漏边界信息 | 冗余度高 |
| **Parent-Child** | 小 chunk 检索 → 大 chunk 返回 | 兼顾检索精度和上下文 | 索引结构复杂 |
| **Late Chunking** | 先 embed 全文，再 pool 到 chunk | 保留全局上下文 | 需要长上下文模型 |
| **标题/元数据前置** | 将父标题拼入每个 chunk 文本 | 低成本、有效 | 仅适用于有层级结构的文档 |

**本项目已实现**：`_extract_title()` + chunk 前置标题（`_split_by_articles` 中 body = `f"{title}\n{seg}"`）

### 1.7 超长段落/章节处理

本项目的 `_fit_window()` 方法实现了业界标准的处理策略：

1. 在句子边界（`。；;；\n`）处切割
2. 贪心拼接直到接近 `ARTICLE_MAX_CHARS`
3. 超长句子按固定步长（`ARTICLE_MAX_CHARS - 80` 或最低 200 字符）硬切割

LlamaIndex 的处理方式类似：`SentenceSplitter` 在句子边界切分，如果单个句子超长，降级为纯字符切割。

---

## 二、评估标准

### 2.1 RAGAS：RAG 评估框架

**仓库**：[`explodinggradients/ragas`](https://github.com/explodinggradients/ragas)  
**文档**：https://docs.ragas.io/

#### Context Recall（上下文召回率）

- **公式**：`supporting_claims / total_claims`（参考答案中能被检索上下文支撑的声明占比）
- **含义**：检索到的上下文是否包含了回答所需的全部信息
- **输入**：`user_input` + `retrieved_contexts` + `reference`
- **与切片的关系**：如果正确信息被切碎分散到多个 chunk 中，可能部分 chunk 未被召回 → 低 recall

```python
# 源码位置：explodinggradients/ragas → src/ragas/metrics/_context_recall.py
class LLMContextRecall(MetricWithLLM, SingleTurnMetric):
    name: str = "context_recall"
```

#### Context Precision（上下文精确率）

- **公式**：rank-aware precision@k 均值
- **含义**：相关 chunk 是否排在 irrelevant chunk 之前
- **输入**：`user_input` + `reference` + `retrieved_contexts`
- **与切片的关系**：大 chunk 含噪声 → 低 precision；小 chunk 过度聚焦 → 可能遗漏但 precision 高

#### Non-LLM 版本

- `NonLLMContextRecall`：使用字符串相似度（Levenshtein / rapidfuzz）比较 retrieved 和 reference contexts
- `NonLLMContextPrecisionWithReference`：同上，但用于精确率计算
- 优点：不需要 LLM 调用，成本低；缺点：仅适用于有 reference contexts 的场景

#### ID-Based 版本

- `IDBasedContextRecall` / `IDBasedContextPrecision`：直接按 chunk ID 比较
- 适合有明确 chunk ID 体系的场景

> **关键结论**：RAGAS 衡量的是**检索端到端质量**，不是 chunk 本身的质量。但在提问集固定的前提下，相同检索策略下的 recall/precision 变化可以反映切片策略的优劣。

### 2.2 DeepEval

**仓库**：[`confident-ai/deepeval`](https://github.com/confident-ai/deepeval)  
**文档**：https://docs.confident-ai.com/

#### ContextualRelevancyMetric（上下文相关性）

- **公式**：`relevant_statements / total_statements`（检索上下文中与问题相关的声明占比）
- **含义**：检索到的上下文中有多少是真正有用的（signal-to-noise ratio）
- **特点**：无需 reference（无 ground truth 也可以评估），纯凭 LLM 判断
- **与切片的关系**：大 chunk 含过多无关内容 → low relevancy

#### 与 RAGAS 互补

| 维度 | RAGAS | DeepEval |
|------|-------|----------|
| Context Recall | ✓ (有 reference) | ✓ (ContextualRecallMetric) |
| Context Precision | ✓ | — |
| Context Relevancy | — | ✓ (无需 reference) |
| Faithfulness | ✓ | ✓ |
| Answer Relevancy | ✓ | ✓ |

### 2.3 Chunk 级别质量评估（研究空白）

**核心发现：当前业界缺少针对单个 chunk 质量的标准化评估指标。**

调研中发现，包括 RAGAS、DeepEval、TruLens 在内的所有主流评估框架都聚焦于**检索质量**（"给定 query，retrieved chunks 是否包含了答案"），而非**chunk 固有质量**（"这个 chunk 本身是不是一个好的信息单元"）。

#### 已知的间接/非标准方法

1. **Chunk 语义完整性**：可通过 LLM 评分评估 chunk 是否可独立理解（不需查阅前后文）
2. **边界质量**：比较切分点与人工标注的语义边界，计算 IoU 或编辑距离
3. **内聚性**：chunk 内句子间的 average cosine similarity（高内聚 = 好 chunk）
4. **分离度**：相邻 chunk 间的 cosine similarity（高于阈值说明本应属于同一 chunk）

#### rag-chunk 工具的评估方法

[`messkan/rag-chunk`](https://github.com/messkan/rag-chunk) 提供了实用的轻量级评估方案：

- **Test file 格式**：每个问题配 `relevant` 关键词列表
- **Retrieval-based recall**：检索 top-k chunk，检查 relevant 关键词命中率
- **支持的指标**：Precision、Recall、F1-score
- **局限性**：依赖关键词匹配（非语义匹配），但启用 `--use-embeddings` 后改进

```json
{
  "questions": [
    {
      "question": "养老保险缴费比例是多少？",
      "relevant": ["养老保险", "缴费比例", "单位", "个人"]
    }
  ]
}
```

#### RAG-Chunking-Benchmark 的多维度评估

[`harishkumard24/RAG-Chunking-Benchmark`](https://github.com/harishkumard24/RAG-Chunking-Benchmark) 实现了 8 种切片策略的系统对比：

- **评估维度**：语义检索精度、检索召回率、生成质量（含 LLM judge）
- **加权排名**：不同维度赋予不同权重，给出综合推荐
- **可视化**：通过 Streamlit 展示 Plotly 对比图

### 2.4 推荐的评估实践路径

根据调研，对本项目最实用的评估流程：

```
1. 构建金标测试集（问题 → 预期 chunk / 预期答案中应包含的法律条文）
2. 对每种切片策略运行相同 RAG pipeline
3. 用 RAGAS (Context Recall + Context Precision) 衡量检索质量
4. 用 LLM-as-judge 评估答案质量（Faithfulness + Answer Relevancy）
5. 对比不同策略的 chunk 数量、平均 chunk 大小（成本维度）
```

---

## 三、工具与资源汇总

### 3.1 切片工具

| 工具 | 策略数 | 特点 | 链接 |
|------|--------|------|------|
| **LangChain** | 递归 + 语言感知 | 最广泛使用的 text-splitters 库 | [langchain-ai/langchain](https://github.com/langchain-ai/langchain) |
| **LlamaIndex** | Sentence/Token/Semantic | 语义分割器是差异化亮点 | [run-llama/llama_index](https://github.com/run-llama/llama_index) |
| **chonkie** | Token/Sentence/Recursive/Semantic/Late | 高性能、低依赖 | [chonkie-ai/chonkie](https://github.com/chonkie-ai/chonkie) |
| **semchunk** | 统计语义 | 低成本语义分割 | [umarbutler/semchunk](https://github.com/umarbutler/semchunk) |
| **rag-chunk** | 6 种 | CLI 测试工具 | [messkan/rag-chunk](https://github.com/messkan/rag-chunk) |
| **Unstructured** | 文档类型感知 | PDF/DOCX/HTML 结构化解析 | [Unstructured-IO/unstructured](https://github.com/Unstructured-IO/unstructured) |

### 3.2 评估工具

| 工具 | 核心指标 | 链接 |
|------|---------|------|
| **RAGAS** | Context Recall, Context Precision, Faithfulness, Answer Relevancy | [explodinggradients/ragas](https://github.com/explodinggradients/ragas) |
| **DeepEval** | ContextualRelevancy, ContextualRecall, Faithfulness, Hallucination | [confident-ai/deepeval](https://github.com/confident-ai/deepeval) |
| **RAG-Chunking-Benchmark** | 8 策略对比 + 加权排名 | [harishkumard24/RAG-Chunking-Benchmark](https://github.com/harishkumard24/RAG-Chunking-Benchmark) |
| **RAGScope** | 多维度 RAG pipeline 基准测试 | [ashah5123/RAGScope](https://github.com/ashah5123/RAGScope) |

### 3.3 重要参考文档

- **awesome-rag-production → chunking-strategies.md**：切片策略分类与决策树
  - [Yigtwxx/awesome-rag-production](https://github.com/Yigtwxx/awesome-rag-production/blob/main/chunking-strategies.md)
- **ever-works/awesome-vector-databases → text-chunking-strategies-for-rag.md**：详细策略说明
- **ai-data-engineer-handbook → document_chunking.md**：数据工程视角的切片指南

---

## 四、与本项目的相关性评估

### 4.1 已对齐业界最佳实践的部分

| 项目实现 | 对应业界实践 | 评价 |
|---------|-------------|------|
| `_split_by_articles()` — "第X条"识别 | Document-type aware splitting | 精准、领域适应性强 |
| `_recursive_split()` — RecursiveCharacterTextSplitter | 兜底策略 | 业界默认最佳 |
| `_fit_window()` — 句子边界贪心拼接 | Sentence-aware splitting | 策略合理 |
| 标题前置 `f"{title}\n{seg}"` | Contextual chunking（元数据注入） | 低成本高效 |
| 检索→reranker→LLM 三层架构 | Hybrid retrieval + reranker | 完整的生产级链路 |
| `ARTICLE_MAX_CHARS=800` | 512-1024 tokens 是主流推荐 | 参数合理 |

### 4.2 可借鉴的优化方向

#### 高优先级（投入产出比高）

1. **引入 RAGAS/DeepEval 做质量回归**
   - 在 `tests/run_eval.py` 中增加 Context Recall + Context Precision 指标
   - 每次修改切片参数后自动对比
   - 复杂度：低（RAGAS 是 pip install 即用的库）

2. **增强 chunk 元数据**
   - 当前仅记录 `source`，建议增加：
     - 文档标题（已有，但未作为 structured metadata）
     - 章节/条号（如"第XX条"）
     - 文档类型（法律/行政法规/司法解释）
     - 在 chunk 内靠前位置的前一个 chunk ID（支持上下文追溯）
   - 复杂度：低

3. **chunk_overlap 细化**
   - 当前硬编码 `max(60, ARTICLE_MAX_CHARS // 8)`，建议：
     - 针对"第X条"切分的法律文档：overlap=0（条文天然独立）
     - 针对递归分割的非法律文档：overlap=15-20%
   - 复杂度：低

#### 中优先级（需一定开发量）

4. **Small-to-Big 检索**
   - 索引时存两个版本：小 chunk（当前 800 字符）用于向量匹配，大 chunk（完整条款/章节）返回给 LLM
   - Qdrant 支持 payload 字段存储关联的 big chunk
   - 复杂度：中

5. **语义切片作为备选策略**
   - 对非法律文档（如政策解读、白皮书），当前递归分割可能错过话题边界
   - 可使用 BGE-M3（项目已加载）做 sentence embedding 相似度检测
   - 复杂度：中（需增加 embedding 计算，但模型已就绪）

#### 低优先级（长期探索）

6. **Late Chunking 实验**：适合超长章节，但需要长上下文 embedding 模型
7. **多粒度索引（Multi-Vector）**：同一文档分别以段落级、句子级、短语级索引
8. **Agentic Chunking**：用 LLM 驱动切片决策（成本高，适合高价值文档）

### 4.3 不适用/不建议的方向

| 方向 | 原因 |
|------|------|
| 纯 Token 级固定分割 | 法律文档的"第X条"是天然语义边界，固定大小会破坏完整性 |
| 完全替换为第三方工具（Unstructured） | 项目已有针对中文法律文档的精细优化，通用工具效果不如专用逻辑 |
| 大规模 A/B 测试平台 | 项目作为课程作业，过度工程化不符合定位 |
| Late Chunking in production | 需要长上下文 embedding 模型（如 jina-embeddings-v3），当前 BGE-M3 不支持 |

---

## 五、参考文献

1. LangChain Text Splitters 源码：https://github.com/langchain-ai/langchain/tree/master/libs/text-splitters
2. LlamaIndex Node Parser 文档：https://docs.llamaindex.ai/en/stable/module_guides/loading/node_parsers/
3. RAGAS Context Recall 文档：https://docs.ragas.io/en/latest/concepts/metrics/available_metrics/context_recall/
4. RAGAS Context Precision 文档：https://docs.ragas.io/en/latest/concepts/metrics/available_metrics/context_precision/
5. DeepEval Contextual Relevancy 文档：https://docs.confident-ai.com/docs/metrics-contextual-relevancy
6. rag-chunk CLI 工具：https://github.com/messkan/rag-chunk
7. RAG-Chunking-Benchmark：https://github.com/harishkumard24/RAG-Chunking-Benchmark
8. awesome-rag-production 切片策略：https://github.com/Yigtwxx/awesome-rag-production/blob/main/chunking-strategies.md
9. chonkie 库（Late Chunking）：https://github.com/chonkie-ai/chonkie
10. awesome-vector-databases 文本切片策略：https://github.com/ever-works/awesome-vector-databases
