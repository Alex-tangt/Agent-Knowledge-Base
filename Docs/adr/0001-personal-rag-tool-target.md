# 0001 目标形状：个人 RAG 工具（服务核心 + 适配层 + 旗舰 demo）

Status: accepted

决定项目定位为**可复用的个人 RAG 检索/问答工具**：以 service 层 + 切分策略为可复用核心，通过适配层对外暴露——FastAPI 网页（政策法规问答旗舰 demo）与 MCP 服务器（延后）。不重写为面向大众的通用 RAG 框架。

理由：`backend/services/` 与 `backend/strategies/` 已构成零 FastAPI 依赖的工具核心，重写会丢掉 21 部法条语料、调参与实验知识；且"通用框架"位置拥挤、难在简历上防守，垂直应用提供了可讲述的硬问题（法条切分、混合检索、无依据拒答、引用溯源、评测）。

Considered options:
- A 通用 RAG 框架（弃）——面向不存在的用户过度设计；"又一个 RAG 封装"在面试中难讲故事。
- B 垂直应用做到极致（弃）——不符合用户跨应用复用的诉求。
- C 薄通用核心 + 旗舰 demo（采用）——可复用部分在 service 层 + strategies 中，本文件记录的目标。

Consequences: 未来新增知识域/功能优先落在 service 层与 strategy 层，而不是往适配层或单一应用里塞逻辑。
