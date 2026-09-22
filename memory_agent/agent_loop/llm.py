"""`LLMClient` 的真实现 + **provider 解析**（ADR-0030 D7）。

两条路径：
- **默认 `opencode-server`**：借**主对话**的模型分配——HTTP 调本地 opencode server
  （`opencode serve`，TUI 自带），`tools=[]` **只取文本**；工具由我们的循环执行。
- **`openai-compat`**：自带 key/base_url/model（**测试用 qwen** 走这条；temp/seed 可控 → 确定性）。

解析优先级（高 → 低）：**调用参数 > 进程环境（含 `memory_agent/.env`）> 默认**。

密钥纪律：key 只从**进程环境 / gitignored `.env`** 读；**不落盘、不日志、不回显**（#25）。
本模块**不 import 评测**。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace

from memory_agent.settings import load_env_file

load_env_file()  # 进程环境优先；读取 memory_agent/.env（#22）

PROVIDER_OPENCODE_SERVER = "opencode-server"
PROVIDER_OPENAI_COMPAT = "openai-compat"
PROVIDERS = (PROVIDER_OPENCODE_SERVER, PROVIDER_OPENAI_COMPAT)

ENV_PROVIDER = "MEMORY_AGENT_LLM_PROVIDER"
ENV_BASE_URL = "MEMORY_AGENT_LLM_BASE_URL"
ENV_MODEL = "MEMORY_AGENT_LLM_MODEL"
ENV_API_KEY = "MEMORY_AGENT_LLM_API_KEY"
ENV_TEMPERATURE = "MEMORY_AGENT_LLM_TEMPERATURE"
ENV_SEED = "MEMORY_AGENT_LLM_SEED"

DEFAULT_OPENCODE_SERVER_URL = "http://127.0.0.1:4096"
DEFAULT_TIMEOUT = 120.0


@dataclass(frozen=True)
class ProviderSpec:
    provider: str = PROVIDER_OPENCODE_SERVER
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    temperature: float | None = None
    seed: int | None = None


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    return value if value and value.strip() else None


def _env_float(name: str) -> float | None:
    raw = _env(name)
    try:
        return float(raw) if raw is not None else None
    except ValueError:
        return None


def _env_int(name: str) -> int | None:
    raw = _env(name)
    try:
        return int(raw) if raw is not None else None
    except ValueError:
        return None


def resolve_provider(override: ProviderSpec | None = None) -> ProviderSpec:
    """解析 provider：`override` 的非 None 字段覆盖环境，环境覆盖默认。"""
    env = ProviderSpec(
        provider=(_env(ENV_PROVIDER) or PROVIDER_OPENCODE_SERVER).strip().lower(),
        base_url=_env(ENV_BASE_URL),
        model=_env(ENV_MODEL),
        api_key=_env(ENV_API_KEY),
        temperature=_env_float(ENV_TEMPERATURE),
        seed=_env_int(ENV_SEED),
    )
    if override is None:
        return env
    return replace(
        env,
        provider=override.provider or env.provider,
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
            raise ValueError(f"openai-compat 需要 base_url（env {ENV_BASE_URL}）")
        if not model:
            raise ValueError(f"openai-compat 需要 model（env {ENV_MODEL}）")
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
    """借宿主（主对话）的模型分配：HTTP 调 opencode server，`tools=[]` 只取文本。

    需要本地有在跑的 opencode server：TUI 自带一个，或 `opencode serve --port <p>`。
    实现按 server OpenAPI（`POST /session` + `POST /session/{id}/message`）；**首次接入
    需在真实 server 上验证**（H 的接入冒烟，见 #62）。
    """

    def __init__(self, *, base_url: str | None = None, model: str | None = None,
                 session_id: str | None = None, timeout: float = DEFAULT_TIMEOUT):
        self.base_url = (base_url or DEFAULT_OPENCODE_SERVER_URL).rstrip("/")
        self.model = model
        self.session_id = session_id
        self.timeout = timeout

    def _model_body(self) -> dict | None:
        if not self.model:
            return None
        if "/" in self.model:
            provider_id, model_id = self.model.split("/", 1)
            return {"providerID": provider_id, "modelID": model_id}
        return {"modelID": self.model}

    def _ensure_session(self, client) -> str:
        if self.session_id:
            return self.session_id
        response = client.post(f"{self.base_url}/session",
                               json={"title": "memory-agent runtime"})
        response.raise_for_status()
        data = response.json()
        self.session_id = data["id"]
        return self.session_id

    def complete(self, messages: list[dict[str, str]], **kwargs) -> str:
        import httpx

        text = "\n\n".join(f"{m.get('role', 'user')}: {m.get('content', '')}"
                           for m in messages)
        try:
            with httpx.Client(timeout=self.timeout) as client:
                session_id = self._ensure_session(client)
                body: dict = {"parts": [{"type": "text", "text": text}], "tools": []}
                model = self._model_body()
                if model:
                    body["model"] = model
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
    """按 spec 构造对应实现。"""
    if spec.provider == PROVIDER_OPENAI_COMPAT:
        return OpenAICompatClient(
            base_url=spec.base_url, model=spec.model, api_key=spec.api_key,
            temperature=spec.temperature, seed=spec.seed,
        )
    if spec.provider == PROVIDER_OPENCODE_SERVER:
        return OpencodeServerClient(base_url=spec.base_url, model=spec.model)
    raise ValueError(
        f"未知 provider：{spec.provider!r}（可选：{' / '.join(PROVIDERS)}）"
    )


def resolve_llm_client(override: ProviderSpec | None = None):
    """一步：解析 provider → 构造客户端。"""
    return build_llm_client(resolve_provider(override))
