"""`LLMClient` 的真实现 + **provider 解析**（ADR-0030 D7）。

两条路径：
- **默认 `opencode-server`**：借**主对话**的模型分配——HTTP 调本地 opencode server
  （`opencode serve`，TUI 自带）**只取文本**、不让宿主执行工具；工具由我们的循环执行。
- **`openai-compat`**：自带 key/base_url/model（**测试用 qwen** 走这条；temp/seed 可控 → 确定性）。

解析优先级（高 → 低）：**调用参数 > 进程环境（含 `memory_agent/.env`）> 默认**。
env 名单登记在 `memory_agent/settings.py`（`LLM_ENV_NAMES` / `llm_env`，**调用时**读取）。

密钥纪律：key 只从**进程环境 / gitignored `.env`** 读；**不落盘、不日志、不回显**（#25）。
本模块**不 import 评测**。
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from memory_agent.settings import LLM_ENV_NAMES, llm_env, load_env_file

load_env_file()  # 进程环境优先；读取 memory_agent/.env（#22）

PROVIDER_OPENCODE_SERVER = "opencode-server"
PROVIDER_OPENAI_COMPAT = "openai-compat"
PROVIDERS = (PROVIDER_OPENCODE_SERVER, PROVIDER_OPENAI_COMPAT)

DEFAULT_OPENCODE_SERVER_URL = "http://127.0.0.1:4096"
DEFAULT_TIMEOUT = 120.0


@dataclass(frozen=True)
class ProviderSpec:
    """LLM provider 配置。

    所有字段默认 `None` = **未指定**，由下层（env / 默认值）补齐——这样"调用参数 > 环境 >
    默认"的优先级才成立（若 `provider` 默认写成具体值，任何 override 都会把环境配置吃掉）。
    `api_key` 用 `repr=False`：dataclass 默认 repr 会把 key 带进日志 / traceback（#25）。
    """
    provider: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = field(default=None, repr=False)
    temperature: float | None = None
    seed: int | None = None


def _float_env(name: str) -> float | None:
    raw = llm_env(name)
    try:
        return float(raw) if raw is not None else None
    except ValueError:
        return None


def _int_env(name: str) -> int | None:
    raw = llm_env(name)
    try:
        return int(raw) if raw is not None else None
    except ValueError:
        return None


def resolve_provider(override: ProviderSpec | None = None) -> ProviderSpec:
    """解析 provider：`override` 的**非 None** 字段覆盖环境，环境覆盖默认。"""
    env = ProviderSpec(
        provider=(llm_env("provider") or PROVIDER_OPENCODE_SERVER).strip().lower(),
        base_url=llm_env("base_url"),
        model=llm_env("model"),
        api_key=llm_env("api_key"),
        temperature=_float_env("temperature"),
        seed=_int_env("seed"),
    )
    if override is None:
        return env
    return replace(
        env,
        provider=(override.provider or env.provider),
        base_url=override.base_url or env.base_url,
        model=override.model or env.model,
        api_key=override.api_key or env.api_key,
        temperature=override.temperature if override.temperature is not None else env.temperature,
        seed=override.seed if override.seed is not None else env.seed,
    )


class OpenAICompatClient:
    """OpenAI 兼容端点（如 DashScope qwen）。`temperature` / `seed` 可控 → 可复现。"""

    def __init__(self, *, base_url: str, model: str, api_key: str | None = None,
                 temperature: float | None = None, seed: int | None = None,
                 timeout: float = DEFAULT_TIMEOUT):
        if not base_url:
            raise ValueError(
                f"openai-compat 需要 base_url（env {LLM_ENV_NAMES['base_url']}）")
        if not model:
            raise ValueError(
                f"openai-compat 需要 model（env {LLM_ENV_NAMES['model']}）")
        self.base_url = base_url
        self.model = model
        self.api_key = api_key or "not-needed"
        self.temperature = temperature
        self.seed = seed
        self.timeout = timeout
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI  # 惰性 import（保持顶层轻）
            self._client = OpenAI(base_url=self.base_url, api_key=self.api_key,
                                  timeout=self.timeout)
        return self._client

    def complete(self, messages: list[dict[str, str]], **kwargs) -> str:
        params: dict = dict(model=self.model, messages=messages)
        if self.temperature is not None:
            params["temperature"] = self.temperature
        if self.seed is not None:
            params["seed"] = self.seed
        params.update(kwargs)
        response = self._get_client().chat.completions.create(**params)
        content = response.choices[0].message.content
        return content or ""


class OpencodeServerClient:
    """借宿主（主对话）的模型分配：HTTP 调 opencode server，**只取文本**。

    需要本地有在跑的 opencode server：TUI 自带一个，或 `opencode serve --port <p>`。
    实测契约（2026-10-03，opencode 1.18.34，见 `memory_agent/eval/opencode_server_smoke_62.py`）：
    - `POST /session` → `{id, ...}`；`POST /session/{id}/message` → `{info, parts}`，
      文本在 `parts[].type == "text"` 的 `text` 字段里。
    - `tools` 是 **map**（`{工具名: boolean}`，OpenAPI `additionalProperties: boolean`），
      **不是数组**：传 `[]` 会被判 `400 BadRequest: Expected object | null, got []`。
      这里用 `{"*": False}` 关掉宿主的全部工具（与随包 agent frontmatter 的 `"*": deny` 同义）；
      我们的工具由 `AgentLoop` 自己执行。
    - `model` **必须** `providerID` + `modelID` 同时给（只给 `modelID` → 400）。留空 =
      用宿主默认模型（这正是"借主对话"的本意）。

    `transport` 仅用于测试注入（`httpx.MockTransport`），生产不传。
    """

    def __init__(self, *, base_url: str | None = None, model: str | None = None,
                 session_id: str | None = None, timeout: float = DEFAULT_TIMEOUT,
                 transport=None):
        self.base_url = (base_url or DEFAULT_OPENCODE_SERVER_URL).rstrip("/")
        self.model = model
        self._model_body = self._resolve_model(model)
        self.session_id = session_id
        self.timeout = timeout
        self.transport = transport

    @staticmethod
    def _resolve_model(model: str | None) -> dict | None:
        """`<providerID>/<modelID>` → 请求体；空 = 用宿主默认；缺 `/` → 快速失败。"""
        if not model:
            return None
        if "/" not in model:
            raise ValueError(
                f"opencode-server 的 model 需为 `<providerID>/<modelID>`（当前 {model!r}）；"
                "留空则用宿主默认模型。"
            )
        provider_id, model_id = model.split("/", 1)
        return {"providerID": provider_id, "modelID": model_id}

    def _ensure_session(self, client) -> str:
        if self.session_id:
            return self.session_id
        response = client.post(f"{self.base_url}/session",
                               json={"title": "memory-agent runtime"})
        response.raise_for_status()
        data = response.json()
        self.session_id = data["id"]
        return self.session_id

    @staticmethod
    def _split_messages(messages: list[dict[str, str]]) -> tuple[str | None, str]:
        """system 消息走服务端 `system` 字段；其余按角色拼成文本（保持 role 标签）。"""
        system_parts = [m.get("content", "") for m in messages if m.get("role") == "system"]
        rest = [m for m in messages if m.get("role") != "system"]
        text = "\n\n".join(f"{m.get('role', 'user')}: {m.get('content', '')}" for m in rest)
        system = "\n\n".join(part for part in system_parts if part) or None
        return system, text

    def complete(self, messages: list[dict[str, str]], **kwargs) -> str:
        import httpx

        system, text = self._split_messages(messages)
        body: dict = {"parts": [{"type": "text", "text": text}],
                      "tools": {"*": False}}
        if system:
            body["system"] = system
        if self._model_body:
            body["model"] = self._model_body
        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                session_id = self._ensure_session(client)
                response = client.post(f"{self.base_url}/session/{session_id}/message",
                                       json=body)
                response.raise_for_status()
                payload = response.json()
        except Exception as error:  # noqa: BLE001 - 统一转成可操作的提示
            raise RuntimeError(
                f"无法调用 opencode server（{self.base_url}）：{error}。"
                "确认有在跑的 `opencode serve`（或 TUI），或改用 openai-compat provider。"
            ) from error
        parts = payload.get("parts") or []
        return "\n".join(p.get("text", "") for p in parts
                         if p.get("type") == "text").strip()


def build_llm_client(spec: ProviderSpec):
    """按 spec 构造对应实现（`provider=None` = 默认 opencode-server）。"""
    provider = spec.provider or PROVIDER_OPENCODE_SERVER
    if provider == PROVIDER_OPENAI_COMPAT:
        return OpenAICompatClient(
            base_url=spec.base_url, model=spec.model, api_key=spec.api_key,
            temperature=spec.temperature, seed=spec.seed,
        )
    if provider == PROVIDER_OPENCODE_SERVER:
        return OpencodeServerClient(base_url=spec.base_url, model=spec.model)
    raise ValueError(
        f"未知 provider：{provider!r}（可选：{' / '.join(PROVIDERS)}）"
    )


def resolve_llm_client(override: ProviderSpec | None = None):
    """一步：解析 provider → 构造客户端。"""
    return build_llm_client(resolve_provider(override))
