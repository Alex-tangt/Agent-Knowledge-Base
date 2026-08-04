# RAG 政策法规问答助手 Golden QA 测试集构建方案调研

> 调研日期：2026-07-26
>
> 项目背景：FastAPI + vanilla-JS RAG 政策法规问答助手，覆盖 21 部中文法律法规，已有 53 题基础评测集（`tests/questions.md`），目标建立 30-50 条可复用的 Golden QA 集用于 RAG 全链路评估。

---

## 目录

- [1. RAG 评估数据集构建最佳实践](#1-rag-评估数据集构建最佳实践)
  - [1.1 业界框架方法论](#11-业界框架方法论)
  - [1.2 评估数据集应包含的维度](#12-评估数据集应包含的维度)
  - [1.3 评测集体量统计标准化](#13-评测集体量统计标准化)
- [2. 中文法律领域构建方案](#2-中文法律领域构建方案)
  - [2.1 现成中文法律 QA 评测集调研](#21-现成中文法律-qa-评测集调研)
  - [2.2 轻量级构建方法](#22-轻量级构建方法)
  - [2.3 Ground Truth 定义策略](#23-ground-truth-定义策略)
  - [2.4 无答案/拒答型测试题设计](#24-无答案拒答型测试题设计)
- [3. 具体实施方案](#3-具体实施方案)
  - [3.1 方案 A：LLM 自动生成 + 人工校验（推荐首选）](#31-方案-allm-自动生成--人工校验推荐首选)
  - [3.2 方案 B：基于 RAGAS/DeepEval 的合成数据 Pipeline](#32-方案-b基于-ragasdeepeval-的合成数据-pipeline)
  - [3.3 两种方案对比](#33-两种方案对比)
  - [3.4 工具与脚本清单](#34-工具与脚本清单)
- [4. 数据格式建议](#4-数据格式建议)
  - [4.1 Golden QA 的 JSON Schema](#41-golden-qa-的-json-schema)
  - [4.2 多维度标签体系](#42-多维度标签体系)
  - [4.3 版本管理与增量扩展](#43-版本管理与增量扩展)
- [5. 参考来源](#5-参考来源)

---

## 1. RAG 评估数据集构建最佳实践

### 1.1 业界框架方法论

#### 1.1.1 RAGAS（Retrieval Augmented Generation Assessment）

**论文**：Shahul Es et al., *"Ragas: Automated Evaluation of Retrieval Augmented Generation"* (arXiv:2309.15217, 2023)

RAGAS 的核心理念是 **"reference-free evaluation"** —— 不依赖人工标注的 ground truth 即可评估 RAG 管道。但在构建高质量 Golden QA 测试集时，RAGAS (v0.2+) 也提供了专门的 `TestsetGenerator` 模块，其方法论要点：

1. **Knowledge Graph 驱动的测试集生成**：从源文档中构建知识图谱（节点 = 文档片段，边 = 实体/主题相似度），通过图遍历自动生成不同类型的查询：
   - **Single-Hop 查询**：单文档、事实型的直接检索问题
   - **Multi-Hop 查询**：跨文档、需要综合多源信息的问题
2. **Scenario-based 生成**：每个查询可配置不同的：
   - **Query 长度**：short / medium / long
   - **Query 风格**：正式 / 口语化 / 搜索式
   - **Persona**：不同用户画像（如高级律师、普通市民）
3. **分组生成**：`SimpleQuerySynthesizer`（简单事实）、`MultiHopQuerySynthesizer`（跨文档推理）、`SpecificQuerySynthesizer`（数值/引用精确型）等

**关键特点**：
- 需要提供底层知识库文档（.pdf/.txt/.md）作为输入
- 自动构建 Node → Relationship 的知识图谱
- 为每个生成的 QA 对附带 `reference_contexts`（检索依据的原文片段）

**文档**：https://docs.ragas.io/en/stable/concepts/test_data_generation/rag/

#### 1.1.2 DeepEval（Confident AI）

**Web**：https://docs.confident-ai.com/

DeepEval 提供了 `Synthesizer` 用于从知识库文档自动生成 Golden 评测集，流程如下：

1. **Document Parsing**：将文档按 token 分块（`chunk_size` 默认 1024 tokens），存入向量库
2. **Context Selection**：随机选取分块，用 LLM（`critic_model`）评估每个 chunk 的质量分数（Clarity / Depth / Structure / Relevance），低于阈值的丢弃
3. **Context Grouping**：按余弦相似度对 chunk 分组，形成有意义的上下文组
4. **Golden Generation**：基于上下文组，用 LLM 生成 `(question, expected_output)` 对
5. **Evolution + Filtering**：对生成结果进行复杂度演化（增加推理难度），再过滤低质量结果

**关键特点**：
- 支持 .txt / .pdf / .docx / .md / .mdx 格式
- 可配置 `max_goldens_per_context`（每上下文最多生成几个 QA 对）
- 生成的 Golden 自带 `expected_output` 和 `context`
- 可导出为 CSV / JSON，集成到 CI/CD pipeline 中

**文档**：https://docs.confident-ai.com/docs/synthesizer-generate-from-docs

#### 1.1.3 业界共识要点

综合 RAGAS、DeepEval、LangChain 及多篇工业实践文章，RAG 评估数据集构建的共识要点：

1. **从知识库出发，不是从想象出发**：QA 对应根植于知识库的实际内容，而非凭空设想问题
2. **覆盖查询多样性**：
   - **事实型**（factoid）：直接能在原文定位
   - **推理型**（reasoning）：需综合多段信息
   - **比较型**（comparative）：对比不同法条/文档
   - **边界型**（edge case）：涉及例外条款/特殊情形
   - **拒答型**（out-of-scope）：知识库无覆盖
3. **质量优于数量**：30-50 条高质量 Golden QA 比 200 条低质量自动生成的数据更有评估价值
4. **人工校验是关键环节**：自动生成的数据往往存在"幻觉引用"、"过于简单"、"答案不精确"等问题，必须经过领域专家（或至少项目负责人）校验
5. **版本化管理**：QA 集应与知识库版本绑定，知识库更新后需同步审查 QA 集有效性

### 1.2 评估数据集应包含的维度

一条完整的 RAG Golden QA 记录至少应包含以下字段：

| 维度 | 字段 | 说明 | 必填 |
|------|------|------|------|
| **问题** | `question` | 自然语言问题 | 是 |
| **Ground Truth 答案** | `ground_truth` / `expected_output` | 参考答案（可为自由文本或结构化 JSON） | 是 |
| **答案类型** | `answer_type` | `extractive`（原文可定位）/ `abstractive`（需综合）/ `no_answer`（应拒答） | 推荐 |
| **关联原文** | `reference_contexts` | 知识库中与答案相关的原文片段列表（chunk 原文 + 文档来源 + 法条编号） | 推荐 |
| **问题类型** | `question_type` | 事实型 / 综合型 / 比较型 / 边界型 / 拒答型 | 推荐 |
| **法律领域** | `domain` | 刑法 / 劳动合同法 / 民法典 / ... | 推荐 |
| **难度** | `difficulty` | easy / medium / hard | 可选 |
| **来源法条** | `law_articles` | 引用的具体法条编号列表，如 `["刑法第232条", "刑法第20条"]` | 可选 |
| **预期检索片段 ID** | `expected_chunk_ids` | 当前 chunk 策略下的预期 chunk_id | 可选 |
| **元数据** | `metadata` | 创建时间、创建人、版本、备注 | 推荐 |

### 1.3 评测集体量统计标准化

**30-50 条足够吗？**

对于本项目（大学课程作业级，21 部法律，目标 30-50 条 Golden QA），体量是合理的：

- **最小可评估体量**：业界普遍认为 ~30 条是区分"看起来对"和"实际能打"的下限。少于 20 条的结果通常没有统计意义。
- **RAGAS 官方建议**：至少 50 条测试样本才能获得有统计意义的指标评估。
- **DeepEval 建议**：每类场景至少 5-10 条，对于 3 类场景（事实/综合/拒答），30 条起点合理。
- **本项目对标**：
  - 已有 53 条基础评测题（含 35 条事实型、17 条综合型、11 条无答案型）
  - 建议 Golden QA 集聚焦于 30-50 条**经过人工验证**的高质量条目，而非简单扩充数量

**分层建议**：
- 事实型：15-20 条（覆盖主要法律类别）
- 综合型：10-15 条（跨法条/跨法律推理）
- 拒答型：5-10 条（确保拒答能力被评估）
- 边界/对抗型：3-5 条（模糊表述、口语化提问）

---

## 2. 中文法律领域构建方案

### 2.1 现成中文法律 QA 评测集调研

#### 2.1.1 LawBench（open-compass/LawBench）

**来源**：https://github.com/open-compass/LawBench
**论文**：Fei et al., *"LawBench: Benchmarking Legal Knowledge of Large Language Models"* (arXiv:2309.16289, 2023)

- **规模**：20 个任务，每个任务 500 个示例，共 10,000 条数据
- **三维认知层次**：
  - 法律知识记忆（法条背诵、知识问答）
  - 法律知识理解（阅读理解、命名实体识别、事件检测等 10 个任务）
  - 法律知识应用（法条预测、罪名预测、刑期预测、案例分析、咨询等 8 个任务）
- **数据格式**：JSON，包含 `instruction` / `question` / `answer`
- **可用于借鉴的任务**：
  - 任务 1-1（法条背诵）：可参考其 prompt 设计
  - 任务 1-2（知识问答）：JEC_QA 来源的法律选择题
  - 任务 2-5（阅读理解）：基于 CAIL2019 的法律阅读理解
  - 任务 3-8（咨询）：来自 hualv.com（66law.cn）的真实法律咨询
- **局限性**：LawBench 是为评估 LLM 通用法律能力设计的，面向选择题/分类/回归等任务格式，**不直接适用于 RAG 检索增强评估**。但其中任务 1-1（法条背诵）和 3-2（基于场景的法条预测）的数据格式与 RAG QA 相近，可作为 prompt 设计的参考。

#### 2.1.2 其他可参考的中文法律数据集

| 数据集 | 来源 | 规模 | 与本项目的关联 | 可借鉴之处 |
|--------|------|------|----------------|------------|
| **JEC_QA** (THUNLP) | https://jecqa.thunlp.org/ | 26,365 条法律知识问答 | 法律知识选择题 | 问题表述方式、答案格式 |
| **CAIL2018-2022** | http://cail.cipsc.org.cn/ | 大规模裁判文书 | 包含法条预测、罪名预测、阅读理解等 | 作为额外参考知识源（不可直接做 QA 集） |
| **CrimeKgAssitant** | https://github.com/liuhuanyong/CrimeKgAssitant | 20 万咨询 QA + 856 罪名 KG | 法务咨询问答 | QA 对格式参考（但质量参差不齐） |
| **LEVEN** (THUNLP) | https://github.com/thunlp/LEVEN | 108 种法律事件类型 | 法律事件检测 | 不直接适用于 RAG 评估 |
| **C-Eval 法律子集** | https://cevalbenchmark.com/ | 法律选择题 | 中国法律知识测试 | 不可直接用于领域专项 QA |
| **LegalBench** (HazyResearch) | 美国法律体系 | 多任务 | 仅参考方法论，内容不适用 | 数据集设计方法论 |

#### 2.1.3 结论：不适合直接迁移现成数据集

**核心原因**：现有中文法律评测集（LawBench、C-Eval 等）面向的是 LLM **通用能力**评估（选择题、分类、实体识别），而不是 RAG 系统的**检索质量 + 生成忠实度**评估。

- LawBench 等问题格式与 RAG 场景不匹配（非问答对话形式）
- 现成数据集不会标注"relevance contexts"（检索相关的原文 chunk）
- 知识库内容量身定制——本项目用的是 21 部法律全文，而非裁判文书或选择题

**正确做法**：基于项目已有知识库，通过 LLM 辅助自动生成 + 人工校验的方式构建专用 Golden QA 集。

### 2.2 轻量级构建方法

适合"没有人工标注经验"的小团队（1-2 人）的轻量方案：

#### 2.2.1 方法一：LLM 自动生成候选 QA + 人工校验

**核心思路**：用 LLM 逐份读取法律文档的分段（chunk），为每段生成候选问题，然后人工筛选和修正。

**Step-by-step**：
1. 将知识库中的 21 份法律文档按现有 chunk 策略（~800 字符/article-aware）切分
2. 对每个 chunk，调用 LLM（建议用项目已有的 API）生成 2-3 个候选问题：
   ```
   Prompt: "请根据以下法律条文内容，生成 2-3 个用户可能提出的自然语言问题（用中文口语化表述）。
   同时给出每个问题的参考答案。
   
   法律条文内容：[chunk_text]
   
   返回 JSON 格式：[{"question": "...", "answer": "...", "article_ref": "第X条"}]"
   ```
3. 人工审核：逐条检查问题是否有意义、答案是否准确、引用是否正确
4. 分层补采：确保覆盖不同法律、不同问题类型后，对不足的类型定向补采
5. 结果录入标准化 JSON/YAML

**优点**：
- 成本低，主要耗时为人工审核（预估 1-2 天）
- 生成的问题天然根植于知识库内容
- 灵活可控，可以随时调整 prompt 改变问题风格

**缺点**：
- 生成的候选问题可能过于简单（"某某法第几条是什么"类型居多）
- 需要额外引入问题类型变异（通过修改 prompt 增加推理型/比较型/场景型问题）
- 人工审核需要一定的领域知识

#### 2.2.2 方法二：从已有 chunk 中提取"问答种子" + 改写

**核心思路**：法律文档中的"第X条"天然就是 Q&A 的最小单位。直接从中提取事实型问题，再通过 LLM 改写为不同难度/类型。

**Step-by-step**：
1. 识别每个 chunk 中的"第X条"及其标题（如"【第二十条】正当防卫"）
2. 将法条标题转化为事实型问题："正当防卫的构成要件是什么？"
3. 通过 LLM 改写生成变体：
   - **场景改写**：将抽象法条转化为具体场景问题（"如果我在街上被人攻击，我还手打伤了对方，这算正当防卫吗？"）
   - **推理加深**：加入前提条件/交叉引用（"如果正当防卫明显超过必要限度造成重伤，会负什么刑事责任？"）
4. 人工审核改写结果

#### 2.2.3 分层采样策略

为确保 Golden QA 集覆盖均衡，建议采用以下分层采样策略：

| 采样维度 | 目标 | 实现方式 |
|----------|------|----------|
| **按法律文档** | 每部法律至少 1 条，重点法律 2-3 条 | 按知识库文档清单等距采样 |
| **按问题类型** | 事实型 40-50%、综合型 30-35%、拒答型 15-20%、边界型 5-10% | 在生成/审核时打标签并统计分布 |
| **按难度** | easy 40%、medium 40%、hard 20% | 人工判定或 LLM 辅助评分 |
| **按查询方式** | 口语化 30%、正式/法言法语 40%、搜索关键词型 30% | Prompt 中指定不同 query_style |
| **按 chunk 来源** | 覆盖分则、总则、附则 | 确保采样覆盖法律文档的不同结构部分 |

### 2.3 Ground Truth 定义策略

对于"答案在文档中"的政策法规场景，ground truth 的定义需要区分层次：

#### 2.3.1 答案类型分层

| 类型 | 定义 | Ground Truth 格式 | 示例 |
|------|------|-------------------|------|
| **原文提取型** (extractive) | 答案直接来自某一条/几条的原文 | 原文摘录 + 来源法条引用 | Q: "试用期的上限是几个月？" A: "六个月" + 引用《劳动合同法》第十九条原文 |
| **原文综合型** (abstractive) | 答案需要综合多条/多法原文 | 综合后的参考答案 + 多条原文片段 | Q: "员工被违法辞退可以主张哪些赔偿？" A: 综合《劳动合同法》第四十七条、四十八条、八十七条 |
| **需推理型** (reasoning) | 答案不直接等于原文，需基于原文做推理 | 推理结论 + 推理依据的多条原文 | Q: "试用期三年是否合法？" A: "不合法" + 引用第十九条"试用期最长不超过六个月" |
| **拒答型** (no_answer) | 知识库中无可靠依据 | `null` 或拒答提示 | Q: "2024年新能源汽车补贴怎么申请？" A: null |

#### 2.3.2 Ground Truth 的语义自由度

法律领域的特殊性在于：
- **原文引用必须精确**：法条内容不应被改写或简化
- **参考答案可以灵活**：生成式 RAG 的输出不可能逐字匹配，因此 ground truth 应允许多种合理表述
- **关键要素覆盖比一字不差更重要**：评估时应关注是否正确覆盖了关键信息点（如罪名、刑期、赔偿标准），而非文本匹配度

**建议做法**：
- 每条 Golden QA 同时记录 `golden_answer_full`（完整参考答案）和 `golden_answer_key_points`（关键信息点列表）
- 评估时使用 LLM-as-judge 判断关键信息点覆盖率，而非简单的 ROUGE/BLEU 文本匹配

### 2.4 无答案/拒答型测试题设计

#### 2.4.1 拒答题应该有的特征

| 特征 | 说明 | 示例 |
|------|------|------|
| **跨领域** | 问题完全属于其他领域 | "如何做番茄炒蛋？" |
| **时效性** | 知识库没有该时效信息 | "2025年最新出台的XX政策是什么？" |
| **地域不匹配** | 其他法律体系 | "美国加州离婚财产如何分割？" |
| **虚构法条** | 编造不存在的法条 | "根据XX法第999条..." |
| **事实类无覆盖** | 知识库确实没涉及的合理问题 | "专利申请的具体流程？" (本项目无专利法) |

#### 2.4.2 拒答题的设计原则

1. **区分"硬拒答"与"软拒答"**：
   - **硬拒答**：知识库完全无相关（如跨领域）→ 预期系统直接拒绝
   - **软拒答**：知识库有部分相关但不充分 → 预期系统说明局限性后给出部分信息
2. **边界情况测试**：
   - 问题中包含真实法条引用 + 虚构内容
   - 问题语言模糊导致可能检索到无关 chunk
   - 多义性问题（一个词在法律中有特殊含义，在口语中有其他意思）
3. **不要用过于明显的问题**：
   - "今天天气怎么样" = 过于明显，不值得作为 Golden QA
   - "请问根据中国法律，如何申请美国绿卡？" = 更好的设计（需要系统识别出"美国绿卡不属于中国法律范畴"）

#### 2.4.3 本项目拒答题改进建议

当前 `tests/questions.md` 中有 12 条无答案型问题（含明显的跨领域问题），建议 Golden QA 集中的拒答题更新为更精细的版本：

| 原题 | 建议改进 | 理由 |
|------|----------|------|
| "如何做番茄炒蛋？" | 保留 | 经典跨领域测试 |
| "2024年新能源汽车购置补贴..." | 改为 "2026年新能源汽车补贴政策的最新调整是什么？" | 知识库非时效性，测试系统是否会编造 |
| "向国家知识产权局申请发明专利的具体流程" | 保留 | 合理领域内但无覆盖 |
| "某明星最近的八卦新闻" | 删除或替换 | 过于明显，测试价值低 |
| 新增 | "根据治安管理处罚法，在地铁上吃东西罚款多少？" | 知识库无治安管理处罚法，测试真假法条区分 |

---

## 3. 具体实施方案

### 3.1 方案 A：LLM 自动生成 + 人工校验（推荐首选）

#### 推荐理由

1. **无需额外依赖**：项目已有 LLM API（`API_KEY` + `BASE_URL` + `Model`），直接复用
2. **完全可控**：prompt 可针对法律领域定制，生成结果可逐条审核
3. **学习成本低**：无人工标注经验也能上手（审批而非创作）
4. **与知识库紧密绑定**：生成的问题直接引用原文 chunk，保证答案可溯源

#### Step-by-step 操作流程

**Phase 1：准备（0.5 天）**

1. 从现有 `VectorStoreService` 导出所有 chunk（或直接从 `data/raw/` 导出）
2. 建立 chunk 索引：`{chunk_id: {text, source_file, articles, character_count}}`
3. 按法律分类对 chunk 分组

**Phase 2：批量生成候选 QA（0.5 天，脚本自动运行）**

1. 编写 Python 脚本，遍历所有 chunk
2. 对每个 chunk 调用 LLM API 生成 2-3 个候选 QA 对
3. Prompt 设计要点：
   ```
   你是中文法律领域的问答对生成专家。请根据以下法律条文内容，
   生成 2-3 个自然语言问题及其参考答案。

   要求：
   - 问题覆盖不同难度：至少 1 个直接事实型 + 1 个场景应用型
   - 问题用一般市民的口语化方式提问（非法律专业人士的语言）
   - 答案必须基于提供的条文内容，不可编造
   - 标注每个问题对应的具体法条编号

   法律条文内容：
   {chunk_text}

   返回 JSON 格式（严格 JSON，不要附带 markdown 标记）：
   {
     "qa_pairs": [
       {
         "question": "问题文本",
         "answer": "参考答案",
         "question_type": "factual|scenario|comparative",
         "articles": ["第X条"],
         "difficulty": "easy|medium|hard"
       }
     ]
   }
   ```
4. 单条 chunk 生成约 2-3 秒，21 个文档 × 平均 25 chunks × 3 秒 ≈ 45 分钟
5. 预估产出：~500 条候选 QA 对

**Phase 3：人工审核与筛选（1-2 天）**

1. 去重：移除语义高度重复的问题（可先用 embedding + cosine 相似度预过滤）
2. 逐条审核：
   - [ ] 问题是否有意义（不是无意义的法条背诵）
   - [ ] 答案是否准确（逐条与原文核对）
   - [ ] 法条引用是否正确
   - [ ] 问题类型标签是否合理
3. 初筛目标：100 条高质量候选
4. 终筛目标：30-50 条（按分层覆盖要求精选）

**Phase 4：补充采样（0.5 天）**

1. 统计各维度的覆盖情况（法律、问题类型、难度）
2. 对不足的类别定向生成补采
3. 人工补充 5-10 条拒答型+边界型题目（建议手工编写，质量更高）

**Phase 5：格式化与入库（0.5 天）**

1. 统一为 JSON 格式（见第 4 节）
2. 添加元数据（版本号、创建时间、审核人）
3. Git 提交到 `tests/golden_qa_v1.json` 或 `tests/golden_qa/` 目录

**预估总投入**：3-4 人天（1 人执行）

### 3.2 方案 B：基于 RAGAS/DeepEval 的合成数据 Pipeline

#### 推荐理由

1. **自动化程度高**：文档→QA 对全链路自动化
2. **生态完整**：生成+评估一体化，可直接接入后续评估 pipeline
3. **Knowledge Graph 方法**（RAGAS）：可自动生成 multi-hop 问题
4. **Evolution 机制**（DeepEval）：自动增加问题复杂度的多样性

#### Step-by-step 操作流程

**Phase 1：环境准备（0.5 天）**

```bash
pip install ragas  # 或 deepeval
# 配置 LLM（复用项目 API）
# 配置 embedding（复用项目 BGE-M3）
```

**Phase 2：RAGAS 方案详细步骤（1 天）**

```python
from ragas.testset import TestsetGenerator
from ragas.llms import LangchainLLMWrapper
from ragas.embeddings import LangchainEmbeddingsWrapper

# 配置 LLM（使用项目的 API）
generator = TestsetGenerator(
    llm=LangchainLLMWrapper(your_llm),
    embedding_model=LangchainEmbeddingsWrapper(your_embedding)
)

# 从文档生成测试集
testset = generator.generate_with_langchain_docs(
    documents=law_documents,  # 知识库的所有法律文档
    testset_size=50,
    with_debugging_logs=True
)

# 导出
testset.to_json("tests/golden_qa_ragas.json")
```

**DeepEval 方案**：

```python
from deepeval.synthesizer import Synthesizer

synthesizer = Synthesizer()
goldens = synthesizer.generate_goldens_from_docs(
    document_paths=["data/raw/中华人民共和国刑法.md", ...],
    max_goldens_per_context=2
)

# goldens 为 Golden 对象列表，含 input, expected_output, context
```

**Phase 3：人工审核（1 天）**

- 审核自动生成的 QA 对，修正不准确之处
- 补充拒答型问题（自动化工具不擅长生成此类）
- 调整问题类型的分布平衡

**预估总投入**：2-3 人天（1 人执行，含调试排错时间）

### 3.3 两种方案对比

| 维度 | 方案 A（LLM + 人工） | 方案 B（RAGAS/DeepEval） |
|------|---------------------|-------------------------|
| **学习成本** | 低（只需写 prompt） | 中（需了解框架 API） |
| **代码量** | ~100 行 Python | ~30 行 Python |
| **质量可控性** | 高（每步可审查） | 中（黑盒生成） |
| **Multi-hop 能力** | 需手动设计 | 自动支持（RAGAS KG 方法） |
| **额外依赖** | 无 | ragas / deepeval + chromadb |
| **中文法律适配度** | 高（prompt 可控） | 中（通用生成器） |
| **生成问题多样性** | 依赖 prompt 设计 | 框架自动提供变异 |
| **时间投入** | 3-4 人天 | 2-3 人天 |
| **后续可复用性** | 脚本可复用 | pipeline 可复用 |
| **推荐场景** | 快速启动，追求可控 | 长期维护，多个知识库 |

**推荐**：从**方案 A** 开始。原因是：
- 项目已有 LLM API，无需额外安装大型框架
- 法律领域需要精确性，人工逐条审核不可省略（两种方案都需要）
- 30-50 条的体量小，自动化 pipeline 的边际收益不明显
- 后续可逐步引入 RAGAS 做自动评估，而非生成

### 3.4 工具与脚本清单

#### 所需工具

| 工具 | 用途 | 安装 |
|------|------|------|
| 项目已有 LLM API | QA 对生成 | 已配置 |
| Python `json` / `yaml` | 数据序列化 | 内置 |
| `sentence-transformers` | chunk 去重（semantic dedup） | 已安装（BGE-M3） |
| `numpy` / `sklearn` | 余弦相似度计算 | 已有 |
| **(可选)** `ragas` | 自动评估 | `pip install ragas` |
| **(可选)** `deepeval` | 合成数据生成 | `pip install deepeval` |

#### 需编写的脚本

| 脚本 | 功能 | 预估行数 |
|------|------|----------|
| `scripts/generate_qa_candidates.py` | 遍历 chunk，调用 LLM 生成候选 QA | ~80 行 |
| `scripts/dedup_qa.py` | 基于 embedding 相似度去重 | ~40 行 |
| `scripts/validate_qa.py` | 校验 JSON 格式 + 统计覆盖率 | ~50 行 |
| `scripts/export_golden_qa.py` | 导出为标准化 JSON / YAML | ~30 行 |

**提示**：建议让 LLM 辅助编写这些脚本。所有脚本放在 `scripts/` 目录下，不影响 `backend/` 代码。

---

## 4. 数据格式建议

### 4.1 Golden QA 的 JSON Schema

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "Golden QA Test Set for RAG Policy & Regulation Assistant",
  "type": "object",
  "properties": {
    "version": {
      "type": "string",
      "description": "语义版本号，格式 MAJOR.MINOR",
      "example": "1.0"
    },
    "created_at": {
      "type": "string",
      "format": "date",
      "description": "创建日期 ISO 8601"
    },
    "updated_at": {
      "type": "string",
      "format": "date"
    },
    "description": {
      "type": "string",
      "description": "数据集用途说明"
    },
    "kb_version": {
      "type": "string",
      "description": "关联的知识库版本/commit hash"
    },
    "items": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/GoldenQAItem"
      }
    },
    "statistics": {
      "type": "object",
      "description": "数据集统计摘要（自动计算）",
      "properties": {
        "total": { "type": "integer" },
        "by_type": { "type": "object" },
        "by_domain": { "type": "object" },
        "by_difficulty": { "type": "object" }
      }
    }
  },
  "$defs": {
    "GoldenQAItem": {
      "type": "object",
      "required": ["id", "question", "ground_truth_answer", "question_type", "domain"],
      "properties": {
        "id": {
          "type": "string",
          "description": "唯一标识符，格式 qa-{序号}",
          "example": "qa-001"
        },
        "question": {
          "type": "string",
          "description": "自然语言问题"
        },
        "ground_truth_answer": {
          "type": "string",
          "description": "参考答案。拒答型置空字符串"
        },
        "answer_type": {
          "type": "string",
          "enum": ["extractive", "abstractive", "reasoning", "no_answer"],
          "description": "Ground truth 的类型"
        },
        "question_type": {
          "type": "string",
          "enum": ["factual", "comprehensive", "comparative", "edge_case", "out_of_scope"],
          "description": "问题类型分类"
        },
        "domain": {
          "type": "string",
          "description": "法律领域，对应知识库文档名（无空格短名）",
          "example": "criminal_law, labor_contract_law, civil_code"
        },
        "law_articles": {
          "type": "array",
          "items": { "type": "string" },
          "description": "引用的法条编号",
          "example": ["刑法第232条", "刑法第20条"]
        },
        "reference_contexts": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "chunk_id": { "type": "string" },
              "text": { "type": "string" },
              "source_file": { "type": "string" },
              "articles": { "type": "array", "items": { "type": "string" } }
            }
          },
          "description": "关联的原文片段。拒答型为空数组"
        },
        "key_points": {
          "type": "array",
          "items": { "type": "string" },
          "description": "答案的关键信息点，用于 LLM-as-judge 评分"
        },
        "difficulty": {
          "type": "string",
          "enum": ["easy", "medium", "hard"],
          "default": "medium"
        },
        "query_style": {
          "type": "string",
          "enum": ["colloquial", "formal", "keyword_search"],
          "description": "查询风格"
        },
        "metadata": {
          "type": "object",
          "properties": {
            "created_by": { "type": "string" },
            "reviewed_by": { "type": "string" },
            "review_status": {
              "type": "string",
              "enum": ["draft", "reviewed", "approved"]
            },
            "notes": { "type": "string" }
          }
        }
      }
    }
  }
}
```

#### 具体示例

```json
{
  "id": "qa-003",
  "question": "我跟公司签了三年合同，试用期写了半年，公司说这是合法的，是真的吗？",
  "ground_truth_answer": "不合法。根据《劳动合同法》第十九条，劳动合同期限三年以上固定期限和无固定期限的劳动合同，试用期不得超过六个月。三年合同试用期可为六个月，但你的情况存在争议——法律表述为'三年以上'，含三年。严格来说六个月试用期可能不违法，但建议关注：（1）同一用人单位与同一劳动者只能约定一次试用期；（2）试用期工资不得低于本单位相同岗位最低档工资或者劳动合同约定工资的80%，并不得低于用人单位所在地的最低工资标准。",
  "answer_type": "reasoning",
  "question_type": "edge_case",
  "domain": "labor_contract_law",
  "law_articles": ["劳动合同法第19条", "劳动合同法第20条"],
  "reference_contexts": [
    {
      "chunk_id": "labor_contract_law_chunk_005",
      "text": "第十九条 劳动合同期限三个月以上不满一年的，试用期不得超过一个月；劳动合同期限一年以上不满三年的，试用期不得超过二个月；三年以上固定期限和无固定期限的劳动合同，试用期不得超过六个月。同一用人单位与同一劳动者只能约定一次试用期。...",
      "source_file": "中华人民共和国劳动合同法.md",
      "articles": ["第十九条", "第二十条"]
    }
  ],
  "key_points": [
    "三年合同试用期上限为六个月",
    "同一用人单位只能约定一次试用期",
    "试用期工资不低于80%且不低于最低工资标准"
  ],
  "difficulty": "hard",
  "query_style": "colloquial",
  "metadata": {
    "created_by": "llm_gen_v1",
    "reviewed_by": "human",
    "review_status": "approved",
    "notes": "边界型问题——'三年以上'是否含三年的歧义，需要系统回答时不给出误导性结论"
  }
}
```

### 4.2 多维度标签体系

#### 法律领域标签

```yaml
domains:
  criminal_law:         "中华人民共和国刑法"
  civil_code:           "中华人民共和国民法典"
  labor_contract_law:   "中华人民共和国劳动合同法"
  labor_law:            "中华人民共和国劳动法"
  company_law:          "中华人民共和国公司法"
  consumer_protection:  "中华人民共和国消费者权益保护法"
  personal_info:        "中华人民共和国个人信息保护法"
  social_insurance:     "中华人民共和国社会保险法"
  food_safety:          "中华人民共和国食品安全法"
  minor_protection:     "中华人民共和国未成年人保护法"
  income_tax:           "中华人民共和国个人所得税法"
  admin_penalty:        "中华人民共和国行政处罚法"
  admin_license:        "中华人民共和国行政许可法"
  road_traffic:         "中华人民共和国道路交通安全法"
  admin_review:         "中华人民共和国行政复议法"
  women_rights:         "中华人民共和国妇女权益保障法"
  anti_telecom_fraud:   "中华人民共和国反电信网络诈骗法"
  admin_compulsion:     "中华人民共和国行政强制法"
  state_compensation:   "中华人民共和国国家赔偿法"
  tax_deduction:        "个人所得税专项附加扣除暂行办法"
  residence_permit:     "居住证申领办事指南"
```

#### 问题类型标签

```yaml
question_types:
  factual:          "事实型——单个法条的明确答案，可直接在原文定位"
  comprehensive:    "综合型——需要综合多条或多部法律的信息"
  comparative:      "比较型——对比不同法条/法律的规定"
  edge_case:        "边界型——涉及例外条款、模糊地带、需推理判断"
  out_of_scope:     "拒答型——知识库无可靠依据"
```

#### 难度标签

```yaml
difficulty:
  easy:     "单次检索即可回答，答案明确"
  medium:   "需要综合 2-3 处信息或进行简单推理"
  hard:     "需要多步推理、跨文档检索、或判断法律条文边界"
```

### 4.3 版本管理与增量扩展

#### 版本号规范

- `MAJOR.MINOR`（如 1.0, 1.1, 2.0）
- **MAJOR 变更**：知识库大幅变更（新增/删除大量文档）、数据结构变更、标签体系变更
- **MINOR 变更**：新增/修改/删除个别 QA 条目、修正 ground truth

#### 文件组织

```
tests/
├── golden_qa/
│   ├── v1/
│   │   ├── golden_qa_v1.0.json          # 完整数据集
│   │   ├── golden_qa_v1.0_schema.json   # JSON Schema（自动校验用）
│   │   └── CHANGELOG_v1.md              # v1.x 变更记录
│   ├── golden_qa_latest.json            # 快捷链接 → 最新版本
│   └── README.md                        # 数据集说明
├── questions.md                         # 保留旧评测题（向后兼容）
├── run_eval.py                          # 评测脚本
├── score_eval.py                        # LLM-as-judge 评分
└── results.md                           # 评测结果
```

#### 增量扩展策略

| 场景 | 操作 | 版本变化 |
|------|------|----------|
| 新增 1 部法律到知识库 | 从该法律生成 2-4 条 QA 并审核 | MINOR bump |
| 丰富问题类型（如增加 comparative 类型） | 对现有知识库针对性生成新类型问题 | MINOR bump |
| 修正某条 QA 的 ground truth 错误 | 直接修改 + 记录 changelog | MINOR bump |
| 重构标签体系 | 全量更新所有条目标签 | MAJOR bump |
| 知识库大规模重构 | 重新生成 QA 集 | MAJOR bump（新 v2.0） |
| 日常发现优质用户问题 | 手工录入 + 标注 | MINOR bump |

#### Changelog 模版

```markdown
# Golden QA Changelog

## v1.1 (2026-08-15)
### Added
- qa-051: 新增《反电信网络诈骗法》场景型问题
- qa-052: 新增 comparative 类型问题（刑法 vs 治安管理处罚法）
### Changed
- qa-003: 修正 ground_truth_answer 中对试用期的判断逻辑
### Removed
- qa-040: 删除——与 qa-038 语义重复过多

## v1.0 (2026-08-01)
### Added
- 初始版本，包含 40 条 Golden QA
- 覆盖 18 部法律，4 种问题类型
```

---

## 5. 参考来源

### 学术论文
1. Fei, Z. et al. *"LawBench: Benchmarking Legal Knowledge of Large Language Models"*. arXiv:2309.16289, 2023. https://arxiv.org/abs/2309.16289
2. Es, S. et al. *"Ragas: Automated Evaluation of Retrieval Augmented Generation"*. arXiv:2309.15217, 2023. https://arxiv.org/abs/2309.15217
3. Yao, F. et al. *"LEVEN: A Large-Scale Chinese Legal Event Detection Dataset"*. ACL 2022 Findings. https://aclanthology.org/2022.findings-acl.17/

### 开源框架/工具
4. **RAGAS** — RAG 评估框架（含 testset generation）。https://github.com/explodinggradients/ragas | 文档：https://docs.ragas.io/
5. **DeepEval** — LLM 评估框架（含 Synthesizer）。https://github.com/confident-ai/deepeval | 文档：https://docs.confident-ai.com/
6. **LawBench** — 中文法律 LLM 评测基准。https://github.com/open-compass/LawBench | 数据：https://huggingface.co/opencompass

### 中文法律数据集
7. **JEC_QA** — 法律知识问答数据集。https://jecqa.thunlp.org/
8. **CrimeKgAssitant** — 罪名知识图谱与法务问答。https://github.com/liuhuanyong/CrimeKgAssitant
9. **CAIL** (Chinese AI and Law challenge) 系列比赛。http://cail.cipsc.org.cn/
10. **LEVEN** — 大规模中文法律事件检测数据集。https://github.com/thunlp/LEVEN

### 评测方法论文档
11. RAGAS Testset Generation for RAG: https://docs.ragas.io/en/stable/concepts/test_data_generation/rag/
12. DeepEval Synthesizer - Generate From Docs: https://docs.confident-ai.com/docs/synthesizer-generate-from-docs
13. DeepEval Evaluation Datasets: https://docs.confident-ai.com/docs/evaluation-datasets

### 本项目已有资料
14. `tests/questions.md` — 当前 53 题基础评测集（事实型 35 / 综合型 17 / 无答案型 11 + 12）
15. `data/SOURCES.md` — 知识库数据来源清单（21 个文档）
16. `backend/services/` — RAG 全链路实现（参考评估维度设计）
17. `tests/run_eval.py` / `tests/score_eval.py` — 现有评测脚本（理解评估流程后对接 Golden QA）

---

> **下一步建议**：
> 1. 确认方案选择（推荐方案 A）
> 2. 编写 `scripts/generate_qa_candidates.py`
> 3. 运行生成 → 人工审核 → 产出 v1.0 Golden QA 集
> 4. 基于 Golden QA 对接现有 `run_eval.py` 或 `score_eval.py` 评估 pipeline
