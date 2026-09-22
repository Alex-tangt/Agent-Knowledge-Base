// memory-agent 形态 B：插件工具 memory_research（#61 / ADR-0026 追加 D10 补充）
//
// 在插件工具的 execute 内建**子会话**并驱动 `memory-research` 子代理：
// 多跳检索过程不进主对话；**阻塞**（与原生 subagent 默认前台一致，await 子会话）；
// **hop 预算由本工具控**（代码循环，非模型自选）。返回**纯文本**（结论 + id / INSUFFICIENT）。
//
// hop 协议：子代理若需追加检索，只回一行 `NEXT_QUERY: <聚焦的新 query>`；否则给结论。
// 本工具据此续跳，用满 max_hops（默认 2）即止。
//
// 信任级：本文件运行在 **opencode 宿主进程内**（同进程、全权）。只读记忆工具由子代理的
// agent 权限白名单收口（见 memory_agent/agent/memory-research.md），本插件自身不写记忆。
import { tool } from "@opencode-ai/plugin"

const AGENT = "memory-research"
const DEFAULT_MAX_HOPS = 2
const NEXT_QUERY_RE = /^[ \t]*NEXT_QUERY[ \t]*:[ \t]*(.+)$/im

// SDK 返回可能是 { data, ... } 或直接是 data（responseStyle 差异），两种都兜住。
function unwrap(res) {
  if (res && typeof res === "object" && "data" in res && res.data) return res.data
  return res
}

function textOf(message) {
  const parts = (message && message.parts) || []
  return parts
    .filter((p) => p && p.type === "text" && typeof p.text === "string")
    .map((p) => p.text)
    .join("\n")
    .trim()
}

function progress(ctx, title) {
  try {
    if (ctx && typeof ctx.metadata === "function") ctx.metadata({ title })
  } catch {
    // 进度是尽力而为，失败不影响检索。
  }
}

function buildPrompt(query, round, maxHops) {
  const protocol =
    "（检索协议：若仍需追加检索，**只回一行** `NEXT_QUERY: <聚焦的新 query>`，不要给结论；" +
    "否则给出结论 + 依据 id 列表；无证据回 `INSUFFICIENT`。" +
    `最多追加检索 ${maxHops} 次。）`
  if (round === 0) return `${query}\n\n${protocol}`
  return `基于你已检索到的证据，继续检索这个缺口：${query}\n\n${protocol}`
}

export const MemoryResearchPlugin = async ({ client }) => {
  return {
    tool: {
      memory_research: tool({
        description:
          "多跳记忆检索（阻塞）：在独立子会话里用 memory-research 子代理检索全局记忆与只读语料，" +
          "返回结论与依据 id，检索过程不进主对话。hop 预算由本工具控制。" +
          "适合跨条目 / 多事实 / 多约束的问题；query 需自足（代词 / 省略先消解）。",
        args: {
          query: tool.schema.string().describe("自足的自然语言检索问题"),
          max_hops: tool.schema
            .number()
            .optional()
            .describe("追加检索上限（默认 2，即共 ≤3 轮）"),
        },
        async execute(args, ctx) {
          const maxHops = Number.isFinite(args.max_hops)
            ? Math.max(0, Math.floor(args.max_hops))
            : DEFAULT_MAX_HOPS
          const started = Date.now()
          try {
            const created = unwrap(
              await client.session.create({
                body: {
                  parentID: ctx.sessionID,
                  title: `memory_research: ${String(args.query).slice(0, 48)}`,
                },
              }),
            )
            const sessionID = created && created.id
            if (!sessionID) {
              return `记忆检索失败：无法创建子会话（${JSON.stringify(created)}）。`
            }

            let query = args.query
            let hops = 0
            let last = ""
            for (;;) {
              if (ctx && ctx.abort && ctx.abort.aborted) {
                return `${last || ""}\n\n[memory_research: 已被用户中断]`.trim()
              }
              progress(ctx, `记忆检索中…（第 ${hops + 1} 轮）`)
              const res = unwrap(
                await client.session.prompt({
                  path: { id: sessionID },
                  body: {
                    agent: AGENT,
                    parts: [{ type: "text", text: buildPrompt(query, hops, maxHops) }],
                  },
                }),
              )
              last = textOf(res)
              const match = NEXT_QUERY_RE.exec(last)
              if (!match || hops >= maxHops) break
              const next = match[1].trim()
              if (!next || next === query) break
              query = next
              hops += 1
            }

            const elapsed = ((Date.now() - started) / 1000).toFixed(1)
            if (!last) return "INSUFFICIENT"
            return `${last}\n\n[memory_research: hops=${hops + 1} elapsed=${elapsed}s]`
          } catch (error) {
            const message = error && error.message ? error.message : String(error)
            return `记忆检索失败：${message}（确认 memory-agent 已安装且 memory-research 子代理可用）`
          }
        },
      }),
    },
  }
}
