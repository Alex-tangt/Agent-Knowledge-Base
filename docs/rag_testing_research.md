# RAG Testing Practices: A Primary-Source Research Report

> **Date**: 2026-07-23
> **Scope**: How the RAG community tests their systems — unit testing, end-to-end evaluation, test data construction, and best practices. All findings cite primary sources only (official docs, papers, source code repos).

---

## Table of Contents

1. [Unit Testing in RAG](#1-unit-testing-in-rag)
2. [End-to-End Testing / Evaluation](#2-end-to-end-testing--evaluation)
3. [Test Data Construction Methods](#3-test-data-construction-methods)
4. [Best Practices & Patterns](#4-best-practices--patterns)
5. [Summary Table](#5-summary-table)
6. [References](#6-references)

---

## 1. Unit Testing in RAG

### 1.1 What do people actually unit test?

Analysis of the **LangChain** test suite structure (`langchain-ai/langchain`, `libs/core/tests/unit_tests/` [^1]) reveals that framework-level unit tests target these pure-logic components with no external service dependencies:

| Component | What's tested | Example test files in LangChain |
|-----------|--------------|-------------------------------|
| **Prompt templates** | Template variable substitution, format validation, serialization/deserialization | `unit_tests/prompts/`, `test_prompt_values.py` |
| **Output parsers** | Structured output parsing, error handling on malformed LLM output | `unit_tests/output_parsers/` |
| **Document loaders** | File format parsing, encoding handling, metadata extraction | `unit_tests/document_loaders/` |
| **Documents** | Document object construction, metadata access, transformations | `unit_tests/documents/` |
| **Messages** | Message role assignment, content serialization, multi-modal content blocks | `unit_tests/messages/` |
| **Embedding abstractions** | Interface contracts, dimension validation (not actual embedding computation) | `unit_tests/embeddings/` |
| **Retrievers** | Interface compliance, composition patterns (e.g., `EnsembleRetriever`) | `test_retrievers.py` |
| **Vector store abstractions** | Filter construction, similarity score types, metadata query syntax | `unit_tests/vectorstores/` |
| **Language model abstractions** | Token counting, message formatting, tool-calling schema validation | `unit_tests/language_models/` |
| **Tools** | Tool definition schema, argument schema parsing, structured tool decorators | `test_tools.py` |
| **Runnables** | Chain composition correctness, config passing, stream/batch/invoke semantics | `unit_tests/runnables/` |
| **Callbacks / tracers** | Event ordering, handler registration, trace context managers | `unit_tests/callbacks/`, `unit_tests/tracers/` |

**Key observation**: LangChain unit tests _do not_ test actual embedding vectors, actual LLM outputs, or actual vector store query results. They test the _abstractions and interfaces_ — valid constructors, correct serialization, proper error propagation, and chain composition logic.

### 1.2 Known patterns for mocking

LangChain provides a dedicated **`fake/` directory** (`libs/core/tests/unit_tests/fake/`) [^1] containing mock implementations used across unit tests. The pattern is:

**Pattern 1: Fake LLM / Fake Chat Model**
```python
# From LangChain's test infrastructure
class FakeListLLM(LLM):
    """LLM that returns items from a pre-configured list."""
    responses: List[str]
    def _call(self, prompt: str, ...) -> str:
        return self.responses[self.i % len(self.responses)]
```
*Source: LangChain `libs/core/tests/unit_tests/fake/` directory and `conftest.py`* [^1]

**Pattern 2: Fake Embeddings**
```python
class FakeEmbeddings(Embeddings):
    """Returns deterministic embeddings (size-based)."""
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return [[float(len(text))] * 10 for text in texts]
```
*Source: LangChain `unit_tests/fake/`* [^1]

**Pattern 3: Fake Vector Store** — Returns pre-configured documents for any query, enabling deterministic retrieval tests without a running vector database.

**Pattern 4: DeepEval's `pytest` integration** — DeepEval treats evaluation as **pytest-native unit tests** [^2]:
```python
# From DeepEval docs: docs.confident-ai.com/docs/getting-started
from deepeval import assert_test
from deepeval.metrics import GEval

def test_correctness():
    metric = GEval(name="Correctness", criteria="...", threshold=0.5)
    assert_test(test_case, [metric])
```
And runs them with: `deepeval test run test_example.py` [^2]

**Pattern 5: DeepEval's component-level tracing** — Evaluates individual components (retrievers, LLM calls, tool calls) within a trace by attaching metrics to spans via `@observe(metrics=[...])` or `next_llm_span(metrics=[...])` + `CallbackHandler` [^2]. This enables unit-testing individual pipeline stages in isolation.

### 1.3 What pure-logic components are testable?

From the LangChain and DeepEval test infrastructures, the following components are **deterministically testable without external services**:

1. **Text splitters / chunking logic** — Pure function: text in → chunks out. Testable with assert on chunk count, overlap, size boundaries.
2. **Prompt template rendering** — Pure text templating with variable substitution.
3. **Document loading and parsing** — File I/O, encoding, metadata extraction.
4. **Message construction and serialization** — Object construction, JSON/XML serialization.
5. **Output parsing** — LLM output string → structured data. Test with mock LLM outputs.
6. **Tool schema generation** — Function signature → OpenAI/Anthropic tool schema.
7. **Chain composition / runnables** — Pipeline wiring correctness (which component calls which, in what order).
8. **Retriever composition** — `EnsembleRetriever`, `MultiVectorRetriever` logic.
9. **Query rewriting / transformation** — Input query → rewritten query is a pure text transformation. Testable by mocking the LLM and asserting the prompt content.

### 1.4 How does LangChain structure its tests?

LangChain's test hierarchy [^1]:

```
libs/core/tests/
├── unit_tests/          # No external services — pure logic tests
│   ├── fake/            # Mock implementations (FakeLLM, FakeEmbeddings, FakeVectorStore)
│   ├── prompts/
│   ├── output_parsers/
│   ├── documents/
│   ├── embeddings/
│   ├── retrievers/
│   ├── vectorstores/
│   ├── language_models/
│   ├── runnables/
│   ├── tools/
│   ├── messages/
│   ├── callbacks/
│   ├── tracers/
│   └── conftest.py      # Shared fixtures
├── integration_tests/   # Require API keys or external services
└── benchmarks/          # Performance benchmarks
```

Key patterns from LangChain tests [^1]:
- Uses `pytest` fixtures extensively
- Mock LLMs, embeddings, and vector stores via the `fake/` module
- Unit tests validate serialization/deserialization round-trips (prompts, messages, tools)
- Integration tests are separated clearly from unit tests
- Benchmark tests exist but are separate from correctness tests

### 1.5 How does LlamaIndex structure its tests?

LlamaIndex's test hierarchy [^3]:

```
llama-index-core/
├── llama_index/core/    # Source code: LLMs, Vector Stores, Embeddings, Storage, Callables
├── tests/               # Tests directory at same level as source
└── pyproject.toml
```

LlamaIndex's core README [^3] states: "Core classes and abstractions represent the foundational building blocks for LLM applications, most notably, RAG. We've designed the core library so that it can be easily extended through subclasses."

The pattern mirrors LangChain: tests alongside source code, unit/integration separation.

### 1.6 How do people test query rewriting in isolation?

From DeepEval's component-level evaluation pattern [^2], query rewriting can be tested by:
1. Wrapping the rewriting function with `@observe(metrics=[...])`
2. Registering a test case with `update_current_span(test_case=LLMTestCase(...))`
3. Using a metric like `AnswerRelevancyMetric` on the rewritten query

From the RAG testing pattern: mock the LLM call in the rewriting step, then assert the rewritten query's properties (length, keyword presence, language, etc.) as pure-logic tests.

---

## 2. End-to-End Testing / Evaluation

### 2.1 Ragas (Retrieval Augmented Generation Assessment)

**Paper**: "Ragas: Automated Evaluation of Retrieval Augmented Generation" (Shahul Es, Jithin James, Luis Espinosa-Anke, Steven Schockaert), arXiv:2309.15217, Sep 2023 [^4]

**Core contribution**: A **reference-free** evaluation framework — metrics that do not require ground truth human annotations.

> "With Ragas, we put forward a suite of metrics which can be used to evaluate these different dimensions *without having to rely on ground truth human annotations*." [^4]

**RAG Evaluation Metrics** (from Ragas docs v0.4.3) [^5]:

| Metric | What it measures | Requires reference? |
|--------|-----------------|---------------------|
| **Faithfulness** | Whether generated answer is factually grounded in retrieved context | No (reference-free) |
| **Answer Relevance** (Response Relevancy) | Whether answer is relevant to the question | No |
| **Context Precision** | Whether retrieved chunks relevant to the answer are ranked higher | Yes (reference answer) |
| **Context Recall** | Whether all relevant information is retrieved | Yes (reference answer) |
| **Context Entities Recall** | Whether key entities from reference are present in retrieved context | Yes |
| **Noise Sensitivity** | How much irrelevant context degrades answer quality | No |

**Additional metric categories** [^5]:
- **Nvidia Metrics**: Answer Accuracy, Context Relevance, Response Groundedness
- **Agent/Tool metrics**: Topic adherence, Tool call Accuracy, Tool Call F1, Agent Goal Accuracy
- **General purpose**: Aspect critic, Simple Criteria Scoring, Rubrics-based scoring, Instance-specific rubrics
- **Traditional NLP**: BLEU Score, ROUGE Score, Semantic Similarity, Exact Match, String Presence

**Experiments-first approach** (Ragas README [^6]): "Evaluate changes consistently with `experiments`. Make changes, run evaluations, observe results, and iterate to improve your LLM application."

### 2.2 DeepEval

**Source**: DeepEval official docs (`docs.confident-ai.com`) [^2]

**Architecture**: Pytest-native evaluation framework with 50+ research-backed metrics.

**RAG-specific metrics** (from DeepEval's RAG evaluation guide [^7]):

| Layer | Metric | Hyperparameter targeted |
|-------|--------|------------------------|
| **Retrieval** | `ContextualPrecisionMetric` | Reranker ranking quality |
| **Retrieval** | `ContextualRecallMetric` | Embedding model quality |
| **Retrieval** | `ContextualRelevancyMetric` | Chunk size, top-K |
| **Generation** | `AnswerRelevancyMetric` | Prompt template |
| **Generation** | `FaithfulnessMetric` | LLM choice, hallucination |

> "It is no coincidence that these three metrics so happen to cover all major hyperparameters that would influence the quality of your retrieval context." [^7]

**The "RAG Triad"** — reference-free evaluation trio [^7]: `AnswerRelevancyMetric` + `FaithfulnessMetric` + `ContextualRelevancyMetric`. Enables evaluation without labeled `expected_output`.

**Three SOTA evaluation techniques** (from DeepEval homepage [^2]):
- **G-Eval**: Criteria-based, chain-of-thought scoring via form-filling
- **DAG**: Directed-acyclic-graph metrics for objective, multi-step conditional scoring
- **QAG**: Question-Answer Generation for close-ended, reference-grounded scoring

**CI/CD Integration** [^2]:
```bash
deepeval test run test_rag.py
```
Supports GitHub Actions, GitLab CI, Jenkins, CircleCI, Buildkite, Azure Pipelines.

**Component-level tracing evaluation** — DeepEval traces every LLM call, retriever call, and tool call as spans. Metrics attach to individual spans. This enables **component-level scores with per-span timing** [^2]:
```
AGENT plan_refund_strategy  G-Eval          0.94 ✓  220ms
├─ RETRIEVER retrieve_policy  Context Recall  0.89 ✓  68ms
├─ TOOL lookup_order          Faithfulness    1.00 ✓  45ms
└─ LLM gpt-4o classify_intent Answer Relevancy 0.92 ✓  130ms
```

### 2.3 LangSmith Evaluation (LangChain ecosystem)

**Source**: LangSmith docs (`docs.smith.langchain.com`) [^8]

**Evaluation lifecycle** [^8]:
```
Development → Testing → Deployment → Monitoring → Iteration
     ↓           ↓           ↓            ↓           ↓
  Offline     Offline     Online       Online      Both
```

**Two evaluation modes** [^8]:

| Mode | Target | Data | Use case |
|------|--------|------|----------|
| **Offline** | Datasets + Examples | Has reference outputs | Pre-deployment: benchmarking, unit testing, regression testing, backtesting |
| **Online** | Runs + Threads | No reference outputs | Production: real-time monitoring, anomaly detection, feedback collection |

**Evaluation techniques** [^8]:
- **Human**: Annotation queues (single-run, pairwise), inline annotation
- **Code**: Deterministic, rule-based checks (format validation, exact match, compilation)
- **LLM-as-judge**: Reference-free (toxicity, adherence) and reference-based (factual accuracy)
- **Pairwise**: Compare two outputs side-by-side (human or LLM)

**Best practices from LangSmith** [^8]:
- Start with **10–20 manually curated examples** defining "what good looks like"
- Build datasets from: **manually curated** → **historical production traces** → **synthetic data**
- Use **dataset splits** (ML-style, category-based, staged rollout)
- Use **dataset versions** for stability in CI pipelines
- Run **regression tests**: assert new versions outperform baselines on relevant metrics

### 2.4 How do frameworks handle the "generated questions are too precise" problem?

**DeepEval's approach** (from Synthesizer docs [^9]): The `Synthesizer` uses the **Evol-Instruct** method (introduced by WizardML, arXiv:2304.12244 [^10]) to evolve simple questions into more complex and realistic ones across 7 evolution types:

| Evolution | Description | Context-bound? |
|-----------|-------------|----------------|
| `REASONING` | Adds reasoning requirements | No |
| `MULTICONTEXT` | Requires info from multiple chunks | Yes |
| `CONCRETIZING` | Adds concrete details | Yes |
| `CONSTRAINED` | Adds constraints | Yes |
| `COMPARATIVE` | Requires comparison across sources | Yes |
| `HYPOTHETICAL` | Introduces hypothetical scenarios | No |
| `IN_BREADTH` | Broadens scope | No |

Each input goes through `num_evolutions` steps, sampled from a configured distribution. This prevents generated questions from being trivially answerable.

**Ragas's approach** (from Testset Generation docs [^11]): Uses a **Knowledge Graph-based approach**:
1. Documents → hierarchical nodes (chunking)
2. Extractors extract entities, keyphrases from nodes
3. Relationship builders connect related nodes (Jaccard similarity on entities)
4. **Scenario Generation** produces queries varying: Query Length (short/medium/long), Query Style (formal/informal/web search), Persona (coming soon)

This KG approach generates both **single-hop** and **multi-hop** queries, both **specific** (fact-based) and **abstract** (interpretive), covering realistic user query patterns [^11].

### 2.5 What metrics do frameworks use beyond the standard 5?

Beyond the standard Faithfulness/Answer Relevance/Context Precision/Context Recall/Context Relevancy:

**DeepEval metrics** (50+ total) [^2]:
- `GEval`: Custom criteria-based evaluation (any criterion expressible in plain English)
- `DAGMetric`: Multi-step conditional scoring via directed acyclic graphs
- `TaskCompletionMetric`: For agent goal completion
- `ConversationalGEval`: Multi-turn conversation evaluation
- `TurnFaithfulnessMetric` / `TurnContextualRelevancyMetric`: Per-turn RAG metrics for multi-turn systems
- `ToolCallAccuracy` / `ToolCallF1`: Tool-calling evaluation
- Safety metrics: Toxicity, Bias, PII detection
- Red-teaming: Adversarial robustness

**Ragas metrics** (beyond RAG) [^5]:
- `FactualCorrectness` (NLI-based)
- `SemanticSimilarity` (cross-encoder)
- `NoiseSensitivity` (how much irrelevant context degrades quality)
- `ContextEntitiesRecall` (entity-level retrieval evaluation)
- `TopicAdherence` (agent conversations)
- `AgentGoalAccuracy` (did the agent achieve its goal)
- SQL metrics: `ExecutionDatacompyScore`, `SQLQueryEquivalence`
- `SummarizationScore` (coverage, density)

**LangSmith evaluators** [^8]:
- Pairwise comparison with configurable prompts
- Custom Python code evaluators
- Annotation queue human evaluation with structured rubrics

### 2.6 How do frameworks measure latency/timing in RAG pipelines?

**DeepEval's approach** [^2]: Every traced span in `deepeval test run` includes timing:
```
RETRIEVER retrieve_policy_docs(query=…)  Context Recall  0.89 ✓  68ms
TOOL     lookup_order(id="#9281")          Faithfulness     1.00 ✓  45ms
LLM      gpt-4o · classify_intent         Answer Relevancy 0.92 ✓  130ms
```
Timing data is captured per span, visible in the CLI trace tree output (`deepeval inspect`), and stored for regression analysis on Confident AI.

**LangSmith's approach** [^8]: Every run (trace) captures latency metrics natively. `child_runs` each have their own timing. This data is available for offline and online evaluation. Online evaluators can trigger on latency thresholds.

---

## 3. Test Data Construction Methods

### 3.1 Human Annotation (TREC-style relevance judgments)

**When**: LangSmith recommends human evaluation as "often an effective starting point for evaluation" [^8].

**How** (from LangSmith [^8]):
- **Annotation queues**: Structured workflows for human review of production traces
  - **Single-run queues**: Review one run at a time against custom rubric items, support assertions for automated grading
  - **Pairwise queues**: Compare two runs side-by-side (A/B comparisons)
  - Multiple reviewers per run, reservations to prevent conflicts
  - Export annotated runs directly to evaluation datasets

**Scale**: LangSmith recommends starting with **10–20 high-quality examples** covering common scenarios and edge cases [^8].

### 3.2 Synthetic Data Generation from Document Chunks

**DeepEval Synthesizer** (from official docs [^9]):

Four-step pipeline:
1. **Input Generation**: LLM generates questions from provided contexts/documents
2. **Filtration**: Each input scored on self-containment and clarity (0–1), threshold default 0.5, up to 3 retries
3. **Evolution**: Apply `num_evolutions` data evolution steps (Evol-Instruct method from WizardML [^10])
4. **Styling**: Rewrite inputs/outputs to desired formats (e.g., SQL queries, different languages)

Four generation methods [^9]:
- `generate_goldens_from_docs()` — full pipeline from raw files (.txt, .pdf, .docx, .md)
- `generate_goldens_from_contexts()` — from pre-chunked contexts
- `generate_goldens_from_scratch()` — no knowledge base needed
- `generate_goldens_from_goldens()` — augment existing goldens

Customization via config [^9]:
- `FiltrationConfig`: `synthetic_input_quality_threshold`, `max_quality_retries`, `critic_model`
- `EvolutionConfig`: `evolutions` distribution, `num_evolutions`
- `StylingConfig`: `input_format`, `expected_output_format`, `task`, `scenario`
- `ConversationalStylingConfig`: for multi-turn golden generation

Multi-turn support: `ConversationSimulator` generates full conversation turns via a model callback [^7].

**Ragas Testset Generation** (from docs [^11]):

Knowledge Graph-based approach:
1. **Document Splitter**: Chunk documents into hierarchical nodes (custom splitters per domain)
2. **Extractors**: LLM-based (`LLMBasedExtractor`) or rule-based (`Extractor`) — e.g., `NERExtractor`, `KeyphraseExtractor`
3. **Relationship Builders**: e.g., `JaccardSimilarityBuilder` on extracted entities
4. **Transforms**: Apply extractors + builders in sequence or in parallel (`Parallel` class)
5. **Query Synthesizer**: `generate_scenarios()` traverses the KG, `generate_sample()` produces `SingleTurnSample(user_input, reference_contexts, reference)`

Scenario parameters: Nodes, Query Length, Query Style, Persona [^11].

### 3.3 User Query Logs (Production Bootstrapping)

**LangSmith approach** [^8]:
> "Once in production, convert real traces into examples."

Selection strategies:
- **User feedback**: Add runs with negative feedback to test against
- **Heuristics**: Identify interesting runs (e.g., long latency, errors)
- **LLM feedback**: Use LLMs to detect noteworthy conversations

**DeepEval approach** [^2]: Online evaluations run on production traces automatically. Traces can be promoted to datasets for offline evaluation. The `evals_iterator()` pattern runs goldens through the live system, capturing full traces.

### 3.4 Golden Datasets — Known Benchmarks

| Benchmark | Paper | Scope | Source |
|-----------|-------|-------|--------|
| **BEIR** | Thakur et al., arXiv:2104.08663, NeurIPS 2021 [^12] | 18 datasets, 9 retrieval tasks, 10 model architectures evaluated zero-shot | `github.com/UKPLab/beir` |
| **MTEB** | Muennighoff et al., 2022 | Massive Text Embedding Benchmark — 8 tasks, 58 datasets, 112 languages | `huggingface.co/mteb` |
| **KILT** | Petroni et al., 2021 | Knowledge Intensive Language Tasks — 5 tasks, 11 datasets | `github.com/facebookresearch/KILT` |
| **Natural Questions** | Kwiatkowski et al., 2019 | Real Google search queries with Wikipedia answers | `ai.google.com/research/NaturalQuestions` |

**BEIR key findings** [^12]:
> "BM25 is a robust baseline and re-ranking and late-interaction-based models on average achieve the best zero-shot performances, however, at high computational costs. Dense and sparse-retrieval models are computationally more efficient but often underperform other approaches."

### 3.5 Evaluating Query Rewriting Quality

No dedicated metric in major frameworks. However, several approaches can be composed:

**Approach 1: Component-level evaluation via DeepEval** [^2]: Wrap the rewriting function with `@observe(metrics=[...])`, register `LLMTestCase(input=original, actual_output=rewritten)`, and score with `AnswerRelevancyMetric` or custom `GEval`.

**Approach 2: Custom GEval metric** [^2]:
```python
GEval(
    name="Query Rewriting Quality",
    criteria="Determine if the rewritten query preserves the original intent while being more search-friendly and concise.",
    evaluation_params=[LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT],
)
```

**Approach 3: Golden test set**: Create a dataset of (original query, ideal rewritten query) pairs, then evaluate semantic similarity between actual and expected rewriting using Ragas's `SemanticSimilarity` metric [^5] or DeepEval's `GEval`.

---

## 4. Best Practices & Patterns

### 4.1 How production RAG teams do CI/CD for RAG quality

**DeepEval's CI/CD pattern** [^7]:

```yaml
# .github/workflows/rag-testing.yml
name: RAG Testing
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Run deepeval tests
        run: deepeval test run test_rag.py
```

Test file structure [^7]:
```python
# test_rag.py
from deepeval import assert_test
from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric, ...

@pytest.mark.parametrize("test_case", dataset.test_cases)
def test_rag(test_case: LLMTestCase):
    assert_test(test_case, [
        ContextualPrecisionMetric(),
        ContextualRecallMetric(),
        ContextualRelevancyMetric(),
        AnswerRelevancyMetric(),
        FaithfulnessMetric(),
    ])
```

Key features [^2]:
- Each metric has a `threshold` — score below threshold → test fails → CI breaks
- `strict_mode` for stricter evaluation
- `@deepeval.log_hyperparameters()` to track embedding model, chunk size, top-K, temperature per run
- Hyperparameter tracking on Confident AI enables seeing which combination performs best

**LangSmith CI/CD pattern** [^8]:
- Dataset versioning with tags for stable benchmarks
- Experiments compare new versions against baselines
- Regression tests assert new versions outperform or match baselines
- Can run alongside pytest for combined testing

### 4.2 Relationship between unit tests, integration tests, and eval suites

From LangChain's test structure [^1] and DeepEval's architecture [^2]:

```
┌─────────────────────────────────────────────────────────┐
│                    EVAL SUITES                           │
│  (DeepEval test run / Ragas experiments / LangSmith)     │
│  - End-to-end quality assessment                         │
│  - LLM-as-judge metrics                                  │
│  - Run less frequently (nightly, per-release)            │
│  - Expensive (LLM API calls)                             │
├─────────────────────────────────────────────────────────┤
│                  INTEGRATION TESTS                        │
│  (LangChain integration_tests/)                          │
│  - Test with real services (API keys required)           │
│  - Run on PRs if services available                      │
│  - Moderate cost                                         │
├─────────────────────────────────────────────────────────┤
│                    UNIT TESTS                             │
│  (LangChain unit_tests/, DeepEval assert_test)           │
│  - Test pure logic, deterministic components             │
│  - Fake/mock LLMs, embeddings, vector stores              │
│  - Run on every commit                                   │
│  - Fast, cheap, reliable                                 │
└─────────────────────────────────────────────────────────┘
```

LangSmith explicitly separates these concepts [^8]:
> **Evaluation** measures performance according to metrics. Metrics can be fuzzy or subjective, and prove more useful in relative terms. **Testing** asserts correctness. A system can only be deployed if it passes all tests.

> Evaluation metrics can be converted into tests. For example, regression tests can assert that new versions must outperform baseline versions on relevant metrics.

### 4.3 How often do people rerun evaluations?

From LangSmith's evaluation lifecycle [^8]:
- **Development**: Offline evaluations run after every significant change
- **CI/CD**: On every push/PR (automated via `deepeval test run` [^7] or LangSmith CI [^8])
- **Pre-release**: Full benchmark on curated dataset
- **Production**: Online evaluations run **continuously** on live traffic
- **Iteration**: Production issues → new offline test cases → re-evaluate

From Ragas's experiments philosophy [^6]: "Evaluate changes consistently with `experiments`. Make changes, run evaluations, observe results, and iterate."

### 4.4 The "test pyramid" equivalent for RAG

The traditional test pyramid maps to RAG as follows:

```
        /\
       /EV\          RAG EVALUATION SUITES
      /ALS \         (Ragas / DeepEval / LangSmith)
     /      \        Frequency: per-release, nightly
    /────────\       Cost: high (LLM API calls)
   /  INTEG   \      INTEGRATION TESTS
  /  RATION   \     (With real vector DB, embedding model)
 /   TESTS    \    Frequency: per-PR
/──────────────\   Cost: moderate
/     UNIT      \  UNIT TESTS
/     TESTS     \ (Pure logic: splitters, prompts, parsers)
/                \ Frequency: per-commit
────────────────── Cost: negligible
```

Specific RAG additions beyond the standard pyramid:

1. **Golden dataset evaluation**: A curated set of 10–20+ (input, expected_output, reference_context) tuples. Run manually or per-release.
2. **Synthetic data evaluation**: Auto-generated test cases from the knowledge base. Run per-PR or nightly.
3. **Production trace evaluation**: Online evaluators on live traffic. Continuous.
4. **Regression testing**: Compare current vs. baseline metrics. Per-release.
5. **A/B comparison**: Pairwise evaluation between two configurations. Per experiment.

---

## 5. Summary Table

| Question | Answer | Primary Source |
|----------|--------|---------------|
| What's the most mature unit testing pattern? | LangChain's `unit_tests/` + `fake/` structure: mock LLMs, embeddings, vector stores | LangChain `libs/core/tests/unit_tests/fake/` [^1] |
| What's the state-of-the-art E2E eval framework? | Ragas and DeepEval are the two dominant open-source frameworks | Ragas docs [^5], DeepEval docs [^2] |
| How are test datasets constructed? | 4 methods: manual curation → historical traces → synthetic generation (Evol-Instruct or KG-based) | DeepEval Synthesizer [^9], Ragas KG-based [^11], LangSmith [^8] |
| How is "too precise" synthetic data handled? | Data evolution via Evol-Instruct (7 types) or scenario-based generation with varied query length/style | DeepEval EvolutionConfig [^9], Ragas scenarios [^11] |
| How is latency measured? | Per-span timing in trace trees (DeepEval) or run timing (LangSmith) | DeepEval trace output [^2], LangSmith runs [^8] |
| How do teams do CI/CD for RAG? | `deepeval test run` in CI pipeline, LangSmith experiments with dataset versioning | DeepEval CI/CD guide [^7], LangSmith eval concepts [^8] |
| How often should evals run? | Unit tests per-commit, integration per-PR, eval suites per-release/nightly, online evals continuously | LangSmith eval lifecycle [^8] |

---

## 6. References

[^1]: **LangChain test suite**. GitHub: `langchain-ai/langchain`, directory `libs/core/tests/`. Includes `unit_tests/` (with `fake/` mocks), `integration_tests/`, and `benchmarks/`. Accessed 2026-07-23. https://github.com/langchain-ai/langchain/tree/master/libs/core/tests

[^2]: **DeepEval official documentation**. `docs.confident-ai.com`. Getting started guide, tracing overview, metrics listing. Accessed 2026-07-23. https://docs.confident-ai.com/docs/getting-started

[^3]: **LlamaIndex core package**. GitHub: `run-llama/llama_index`, directory `llama-index-core/`. README and test structure. Accessed 2026-07-23. https://github.com/run-llama/llama_index/tree/main/llama-index-core

[^4]: **Ragas paper**: Shahul Es, Jithin James, Luis Espinosa-Anke, Steven Schockaert. "Ragas: Automated Evaluation of Retrieval Augmented Generation." arXiv:2309.15217, Sep 2023 (v2 Apr 2025). https://arxiv.org/abs/2309.15217

[^5]: **Ragas documentation — Available Metrics**. `docs.ragas.io/en/stable/concepts/metrics/available_metrics/`. Lists all metric categories including RAG, Agents, Nvidia, NLP, General Purpose, SQL. Accessed 2026-07-23. https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/

[^6]: **Ragas GitHub repository**. `vibrantlabsai/ragas`. README, features, quickstart. v0.4.3. Accessed 2026-07-23. https://github.com/vibrantlabsai/ragas

[^7]: **DeepEval — RAG Evaluation guide**. `docs.confident-ai.com/docs/guides-rag-evaluation`. Retrieval metrics, generation metrics, CI/CD, multi-turn RAG. Accessed 2026-07-23. https://docs.confident-ai.com/docs/guides-rag-evaluation

[^8]: **LangSmith — Evaluation Concepts**. `docs.smith.langchain.com/evaluation`. Offline/online evaluation, evaluators, datasets, annotation queues, best practices. Accessed 2026-07-23. https://docs.smith.langchain.com/evaluation

[^9]: **DeepEval — Golden Synthesizer documentation**. `docs.confident-ai.com/docs/synthesizer-introduction`. 4-step pipeline, EvolutionConfig, FiltrationConfig, StylingConfig. Accessed 2026-07-23. https://docs.confident-ai.com/docs/synthesizer-introduction

[^10]: **Evol-Instruct / WizardML**: Can Xu et al. "WizardLM: Empowering Large Language Models to Follow Complex Instructions." arXiv:2304.12244, 2023. Data evolution method used by DeepEval Synthesizer. https://arxiv.org/abs/2304.12244

[^11]: **Ragas — Testset Generation for RAG**. `docs.ragas.io/en/stable/concepts/test_data_generation/rag/`. Knowledge Graph-based approach, query types, scenario generation. Accessed 2026-07-23. https://docs.ragas.io/en/stable/concepts/test_data_generation/rag/

[^12]: **BEIR benchmark**: Nandan Thakur, Nils Reimers, Andreas Rücklé, Abhishek Srivastava, Iryna Gurevych. "BEIR: A Heterogenous Benchmark for Zero-shot Evaluation of Information Retrieval Models." NeurIPS 2021. arXiv:2104.08663. https://arxiv.org/abs/2104.08663
