# #62 H 接入冒烟：真实 `opencode serve` 上的 `OpencodeServerClient` 契约

- 日期：2026-10-03
- 对象：`memory_agent/agent_loop/llm.py::OpencodeServerClient`（ADR-0030 D7 的 `opencode-server`
  provider；#62 里"**尚未在真实 server 上做接入冒烟**"这条）
- 脚本：`memory_agent/eval/opencode_server_smoke_62.py`（server 缺席 = SKIP，退出码 0）

## 环境

| 项 | 值 |
|---|---|
| opencode | **1.18.34**（`C:\Users\Tan\AppData\Roaming\npm\opencode.ps1`） |
| server | `opencode serve --port 4096 --pure`（`--pure` = 不加载外部插件，避免本仓插件干扰） |
| 模型 | 宿主默认（响应 `info.modelID = deepseek-flash`；未指定 `model`） |
| OpenAPI | `GET http://127.0.0.1:4096/doc` |

## 结果（6/6 PASS）

```
venv\Scripts\python.exe memory_agent/eval/opencode_server_smoke_62.py --sanitize-proxy-env
```
```
  [PASS] model 缺 providerID（无 `/`）在客户端快速失败
  [PASS] GET /doc 可达（server 在跑）
  [PASS] OpenAPI: `tools` 是 object{additionalProperties:boolean}
  [PASS] OpencodeServerClient.complete 真实往返取回文本
         session=ses_f005205e8ffeKrXAbVw4fb7KPM text='可以'
  [PASS] 同一 client 复用同一 session
  [PASS] 旧形状 tools=[] 被服务端判 400（回归依据）
         HTTP 400 {"name":"BadRequest","data":{"message":"Expected object | null, got []\n  at [\"tools\"]"}}

6/6 checks passed
```

取回文本的码点复核（排除控制台 GBK 管道造成的显示乱码）：

```
repr='可以' codepoints=['0x53ef', '0x4ee5'] len=2
```

## 三条实证结论（都改进了代码）

1. **`tools` 是 map，不是数组。** OpenAPI：
   `"tools": {"type": "object", "additionalProperties": {"type": "boolean"}}`。
   修前实现发 `"tools": []` → **400 `Expected object | null, got []`**——即 `complete()` 在真实
   server 上**根本跑不通**。现在发 `{"*": False}`（关掉宿主全部工具，与随包 agent frontmatter 的
   `"*": deny` 同义；工具由我们自己的循环执行）。检查 6 把这条 400 固化成回归依据。
2. **`model` 必须 `providerID` + `modelID` 成对。** 只给 `modelID` → **400 Missing key at
   ["model"]["providerID"]**；两者都给但取值非法 → 500。修前实现"无 `/` 时只发 `modelID`"是坏的；
   现在 `model` 缺 `/` 在**客户端构造时**即 `ValueError`（快速失败），留空 = 用宿主默认模型
   （这正是"借主对话"的本意）。
3. **`parts[].type == "text"`** 是取文本的位置（响应里还有 `step-start` / `step-finish`），
   `complete()` 的提取方式正确；`system` 消息改走服务端 `system` 字段（此前被拼进 user 文本）。

## 附带发现（宿主 env 问题，不是本仓缺陷）

`NO_PROXY` 里含**带方括号的 IPv6**（本机值 `...,[::1]`）时，httpx 在建 `Client()` 时就抛
`InvalidURL: Invalid port: ':1]'`——**任何** httpx 调用都起不来，错误信息还会被 `complete()` 的
兜底包装成"确认有在跑的 `opencode serve`"，**指向错误的方向**。冒烟脚本因此打印诊断并以
`--sanitize-proxy-env` 提供绕过（剥掉方括号条目）。正确修法是把宿主 `NO_PROXY` 写成 `::1`。

## 离线回归（与冒烟互补，进单测）

`tests/unit/test_agent_loop.py::TestOpencodeServerClient`（`httpx.MockTransport`，不联网）：
`tools` 必须是 dict、`system` 走独立字段、session 只建一次并复用、`model` 缺 `/` 报错、
HTTP 400 转成可操作的 `RuntimeError`。冒烟证明"服务端确实这么收"，单测保证"以后不许改回去"。
