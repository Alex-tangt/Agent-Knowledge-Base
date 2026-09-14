"""Agent-Knowledge-Base 的记忆能力包（MCP + skill）。

读路径最小闭环：把 KB Markdown 条目与只读语料建成条目级派生索引，
stdio MCP 暴露 memory_search / memory_get。见 docs/adr/0006、issue #10。
"""
