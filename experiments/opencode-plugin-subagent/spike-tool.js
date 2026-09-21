// Spike: project-scoped opencode plugin (P0–P3).
//
// 目的：验证「插件工具在 execute 内能否驱动另一个 agent 会话」——核心未知是
// 重入/死锁（在工具调用栈里回调 session.create / session.prompt）。
//
// 范围：本文件在 <worktree>/.opencode/plugins/ —— **只对当前 worktree 生效**，
// 绝不写 ~/.config/opencode/plugins/，也不改全局 opencode 配置。
//
// 证据字段：child session id / parentID / 耗时 / 子会话列表 / 结构化输出 / 子 agent 文本。
import { tool } from "@opencode-ai/plugin"

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
}

export const SpikePlugin = async ({ client, directory, worktree }) => {
  return {
    tool: {
      // ---- P0: 管道探测 -------------------------------------------------
      plugin_ping: tool({
        description: "Spike P0: returns a fixed string; verifies project plugin tools load and are callable.",
        args: { text: tool.schema.string().optional() },
        async execute(args) {
          return `pong:${args.text ?? ""} (dir=${directory})`
        },
      }),

      // ---- P1/P2/P3: 重入探测 -------------------------------------------
      plugin_spawn_probe: tool({
        description:
          "Spike P1: from INSIDE this tool, create a child session and prompt an agent, then return its final text. Tests whether session API calls from a tool re-enter/deadlock. Use it to answer questions via a subagent without polluting the main conversation.",
        args: {
          prompt: tool.schema.string().describe("Message to send to the child agent"),
          agent: tool.schema.string().optional().describe("Agent name to run in the child session (default: build)"),
          structured: tool.schema
            .boolean()
            .optional()
            .describe("P3: also request json_schema structured output {answer: string}"),
        },
        async execute(args, ctx) {
          const t0 = Date.now()
          const out = []
          try {
            const created = unwrap(
              await client.session.create({
                body: { parentID: ctx.sessionID, title: "spike-child" },
              }),
            )
            const id = created && created.id
            if (!id) {
              return `P1 ERROR: session.create returned no id: ${JSON.stringify(created)}`
            }
            out.push(`child=${id}`, `parent=${ctx.sessionID}`)

            const body = {
              agent: args.agent ?? "build",
              parts: [{ type: "text", text: args.prompt }],
            }
            if (args.structured) {
              body.format = {
                type: "json_schema",
                schema: {
                  type: "object",
                  properties: { answer: { type: "string", description: "the answer" } },
                  required: ["answer"],
                },
              }
            }
            const res = unwrap(await client.session.prompt({ path: { id }, body }))
            out.push(`ms=${Date.now() - t0}`)
            const structured = res && res.info && res.info.structured_output
            if (structured) out.push(`structured=${JSON.stringify(structured)}`)
            // DIAGNOSTIC (P3 anomaly): surface the actual response shape.
            out.push(`res-keys=${res && typeof res === "object" ? Object.keys(res).join(",") : typeof res}`)
            out.push(
              `info-keys=${res && res.info && typeof res.info === "object" ? Object.keys(res.info).join(",") : "(no info)"}`,
            )
            if (res && res.info) {
              out.push(`info-structured=${JSON.stringify(res.info.structured ?? null)}`)
              out.push(`info-structured_output=${JSON.stringify(res.info.structured_output ?? null)}`)
              out.push(`info-error=${JSON.stringify(res.info.error ?? null)}`)
            }
            out.push(`parts=${JSON.stringify(((res && res.parts) || []).map((p) => p && p.type))}`)

            let children = []
            try {
              children = unwrap(await client.session.children({ path: { id: ctx.sessionID } })) || []
            } catch (e) {
              children = `children() failed: ${String(e)}`
            }
            out.push(
              `children_of_parent=${Array.isArray(children) ? children.map((c) => c.id).join(",") : String(children)}`,
            )
            out.push("---child-text---", textOf(res))
          } catch (e) {
            out.push(`P1 EXCEPTION after ${Date.now() - t0}ms: ${e && e.stack ? e.stack : String(e)}`)
          }
          return out.join("\n")
        },
      }),
    },
  }
}
