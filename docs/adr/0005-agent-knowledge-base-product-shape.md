# 0005 产品形状升级：Agent 知识库（记忆能力包为首个能力）

Status: accepted (amends ADR-0001)

把项目从"个人 RAG 工具（政策法规旗舰 demo）"升级为 **Agent 知识库**：以 Markdown 知识库作为 agent 的长期知识底座，使其可安全读写、可检索。本阶段 hero = **记忆能力包**（MCP + skill）；政策法规 web（`legal_web`）降为适配层实例与回归锚点，后续升级为 agent 体验用例。北向是**企业级 agent 知识库管理**（多租户访问权限、缓存、降级、内容生命周期），本周冻结不做。

理由：求职目标为 Agent 开发岗，记忆（上下文工程 / 持久化 / 生命周期）差异化最强，且可 dogfood 出真实用户闭环；多跳检索与既有 RAG 能力重叠大、基准指标易被打脸，故降为佐证而非 hero。

Considered options:
- A agent 记忆能力包（采用）——与 Agent 岗同构、可 dogfood、风险可控。
- B 多跳检索 agent（弃，降为佐证）——赌注大（多跳不通即无头条），与既有 RAG 重叠。
- C 双 hero 并列（弃）——注意力分散，叙事不清。

Consequences: 证据重心从"检索指标"转向"写路径确定性 + 检索泛化"；`legal_web` 不再是产品重心；企业级能力显式列为北向而非本周范围。
