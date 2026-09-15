"""config：core / llm 两层配置（ADR-0016）。

- `config.config`：路径 / 模型名 / 检索阈值——无密钥、import 不校验。
- `config.llm`：LLM 凭证与 LangSmith，惰性 `require_llm()`；仅适配层加载其 `.env`。
"""
