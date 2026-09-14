"""用户故事 #17 验收：三个项目仓库只读语料（真实模型，走 MCP stdio 接缝）。

要证明的事（与 issue #7 用户故事 17 对应）：
1. 索引里确实有来自三个仓库的**文档**（source 带 `<label>/` 前缀，writable=false）。
2. 三个仓库各自都能被语义检索命中（不是只索引进去了、搜不到）。
3. 只读语料**结构上不可写**：supersede / archive 直接拒绝。
4. memory_get 能按只读 id 读回真实 Markdown。

只读契约不变：只索引 `.md`（代码不进语料），可写 KB 仍是唯一写入对象。

用法（仓库根）：
    venv\\Scripts\\python.exe memory_agent/eval/readonly_corpus_17.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from mcp import ClientSession  # noqa: E402
from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: E402

SERVER = os.path.join(ROOT, "memory_agent", "mcp_server.py")

# 每个仓库一个探针查询 + 期望出现的 source 前缀（label）。
PROBES = {
    "agent-infra": "agent 工厂、DeepSeek Harness 之上的插件套件与组合配置（profile/patch）",
    "kg-triplet-sft": "知识图谱三元组抽取的 LoRA 训练掩码配方与容量曲线",
    "agent-knowledge-base": "记忆能力包的只读语料与仓库标签消歧义",
}

ROWS: list[dict] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    ROWS.append({"name": name, "ok": bool(ok), "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))
    return bool(ok)


def payload(result) -> object:
    """从 CallToolResult 里取结构化返回（FastMCP 同时给 content 与 structured_content）。"""
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        if isinstance(structured, dict) and set(structured) == {"result"}:
            return structured["result"]
        return structured
    text = result.content[0].text if result.content else ""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, IndexError):
        return text


def is_error(result) -> bool:
    return bool(getattr(result, "is_error", False))


def error_text(result) -> str:
    return result.content[0].text if result.content else ""


async def run() -> list[dict]:
    params = StdioServerParameters(command=sys.executable, args=[SERVER], cwd=ROOT)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = [tool.name for tool in (await session.list_tools()).tools]
            check(
                "1. MCP 暴露 7 个记忆工具",
                {"memory_search", "memory_get", "memory_add", "memory_supersede",
                 "memory_archive", "memory_reindex", "memory_index_status"} <= set(tools),
                f"tools={tools}",
            )

            status = payload(await session.call_tool("memory_index_status", {}))
            check(
                "2. 索引自洽且含三仓库语料",
                status.get("built") and status.get("consistent")
                and status.get("entries") == status.get("points")
                and status.get("entries", 0) >= 100,
                f"gen={status.get('gen')} entries={status.get('entries')} "
                f"points={status.get('points')} consistent={status.get('consistent')}",
            )

            readonly_ids: dict[str, str] = {}
            for label, query in PROBES.items():
                hits = payload(await session.call_tool(
                    "memory_search", {"query": query, "k": 10}
                ))
                matching = [h for h in hits if str(h.get("source", "")).startswith(label + "/")]
                check(
                    f"3.{label} 语义检索命中本仓库文档",
                    bool(matching),
                    f"top={[(h['source'], round(h['score'], 3), h['writable']) for h in hits[:3]]}",
                )
                if matching:
                    readonly_ids[label] = matching[0]["id"]
                check(
                    f"3.{label} 本仓库文档标 writable=false",
                    bool(matching) and all(h["writable"] is False for h in matching),
                    f"readonly_hits={[(h['source'], h['writable']) for h in matching[:3]]}",
                )
                check(
                    f"3.{label} 其它仓库文档未被误标为可写",
                    all(h["writable"] is False
                        for h in hits if str(h.get("source", "")).split("/")[0] in PROBES),
                    f"writable={sorted({h['writable'] for h in hits})}",
                )

            writable_hits = payload(await session.call_tool(
                "memory_search",
                {"query": "记忆架构 Markdown 真相源 派生索引 写入网关", "k": 8,
                 "writable_only": True},
            ))
            check(
                "4. writable_only=true 只返回可写 KB 条目",
                bool(writable_hits) and all(h["writable"] is True for h in writable_hits),
                f"ids={[h['id'] for h in writable_hits[:3]]}",
            )

            readonly_id = next(iter(readonly_ids.values()), None)
            got = payload(await session.call_tool("memory_get", {"entry_id": readonly_id}))
            check(
                "5. memory_get 读回只读条目真实 Markdown",
                isinstance(got, dict) and len(got.get("content", "")) > 0
                and got.get("writable") is False,
                f"id={readonly_id} chars="
                f"{len(got.get('content', '')) if isinstance(got, dict) else '?'}",
            )

            rejected = await session.call_tool("memory_supersede", {
                "old_id": readonly_id, "title": "should not be written",
                "body": "readonly corpus must reject writes", "domain": "topics",
                "type": "topic", "tags": ["readonly"], "confirm": True,
            })
            check(
                "6. supersede 拒写只读语料",
                is_error(rejected) and "只读" in error_text(rejected),
                error_text(rejected)[:80],
            )

            archived = await session.call_tool("memory_archive", {
                "entry_id": readonly_id, "reason": "readonly must reject", "confirm": True,
            })
            check(
                "7. archive 拒写只读语料",
                is_error(archived) and "只读" in error_text(archived),
                error_text(archived)[:80],
            )

    return ROWS


def main() -> int:
    try:
        rows = asyncio.run(run())
    except Exception as exc:  # noqa: BLE001 - 验收脚本要把失败原因打出来
        print(f"运行失败：{exc!r}")
        return 2
    failed = [row for row in rows if not row["ok"]]
    print(f"\n{len(rows) - len(failed)}/{len(rows)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
