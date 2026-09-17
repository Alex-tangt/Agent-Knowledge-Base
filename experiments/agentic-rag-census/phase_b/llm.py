"""DashScope（OpenAI 兼容）LLM 客户端（issue #48）。

- 模型：宿主 `qwen3.7-flash`（源项目 `D:/Study/SFT/kg-triplet-sft` 同款）。
- `temperature=0` + **显式** `enable_thinking=true`（规格要求）。
- key 只从**进程环境** `DASHSCOPE_API_KEY` 读；缺失时可经 `DASHSCOPE_ENV_FILE`
  指向一个 `.env` 惰性加载（**key 绝不落盘 / 不入日志 / 不回显**）。
- 线程安全（`OpenAI` 客户端可并发）；重试仅对网络 / 5xx / 429 等瞬态失败。
- 统一剥离思考块；JSON 解析容错（扫第一个平衡 `{}`，容忍 ```json 围栏）。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Any

BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen3.7-flash"
DEFAULT_JUDGE_MODEL = "qwen3.7-max"  # 答案判定用**不同**模型（防自评）

_THINK_RE = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


class LLMError(RuntimeError):
    pass


def load_api_key() -> str:
    key = os.environ.get("DASHSCOPE_API_KEY")
    if not key:
        env_file = os.environ.get("DASHSCOPE_ENV_FILE")
        if env_file and os.path.isfile(env_file):
            try:
                from dotenv import load_dotenv
            except ImportError:
                load_dotenv = None
            if load_dotenv is not None:
                load_dotenv(env_file)
                key = os.environ.get("DASHSCOPE_API_KEY")
    if not key:
        raise LLMError(
            "DASHSCOPE_API_KEY 未设置（可设 DASHSCOPE_ENV_FILE 指向含该键的 .env）"
        )
    return key


def strip_thinking(text: str) -> str:
    return _THINK_RE.sub("", text or "").strip()


def extract_json_object(text: str) -> dict[str, Any]:
    """从模型输出里抽第一个 JSON 对象（容忍围栏 / 前后缀）。"""
    cleaned = strip_thinking(text)
    fence = _FENCE_RE.search(cleaned)
    if fence:
        cleaned = fence.group(1)
    decoder = json.JSONDecoder()
    for index, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(cleaned[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise LLMError(f"输出中找不到 JSON 对象：{cleaned[:200]!r}")


class DashScope:
    """薄封装：一次调用返回 content / reasoning / usage / 延迟；可选 JSON 解析。"""

    def __init__(self, model: str = DEFAULT_MODEL, *, thinking: bool = True,
                 seed: int = 42, max_retries: int = 4, timeout: float = 180.0,
                 base_url: str = BASE_URL):
        from openai import OpenAI

        self.model = model
        self.thinking = thinking
        self.seed = seed
        self.max_retries = max_retries
        self._client = OpenAI(api_key=load_api_key(), base_url=base_url, timeout=timeout)
        self._lock = threading.Lock()
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    # ------------------------------------------------------------------ stats

    def stats(self) -> dict:
        with self._lock:
            return {
                "calls": self.calls,
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
            }

    def _account(self, usage) -> None:
        with self._lock:
            self.calls += 1
            if usage is not None:
                self.prompt_tokens += int(getattr(usage, "prompt_tokens", 0) or 0)
                self.completion_tokens += int(getattr(usage, "completion_tokens", 0) or 0)

    # ------------------------------------------------------------------- call

    def _once(self, messages: list[dict], max_tokens: int, temperature: float) -> dict:
        kwargs: dict[str, Any] = dict(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            extra_body={"enable_thinking": self.thinking},
            seed=self.seed,
        )
        started = time.time()
        try:
            response = self._client.chat.completions.create(**kwargs)
        except TypeError:
            kwargs.pop("seed", None)
            response = self._client.chat.completions.create(**kwargs)
        message = response.choices[0].message
        content = message.content or ""
        reasoning = getattr(message, "reasoning_content", None)
        if reasoning is None:
            extra = getattr(message, "model_extra", None) or {}
            reasoning = extra.get("reasoning_content")
        self._account(getattr(response, "usage", None))
        return {
            "content": strip_thinking(content),
            "reasoning": reasoning or "",
            "latency_s": round(time.time() - started, 3),
        }

    def chat(self, prompt: str, *, system: str | None = None,
             max_tokens: int = 1024, temperature: float = 0.0) -> dict:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return self._once(messages, max_tokens, temperature)
            except Exception as exc:  # noqa: BLE001 - 瞬态失败重试
                last = exc
                if attempt >= self.max_retries:
                    break
                time.sleep(min(2 ** attempt, 16))
        raise LLMError(f"LLM 调用失败（{self.model}）：{type(last).__name__}: {last}")

    def chat_json(self, prompt: str, *, system: str | None = None,
                  max_tokens: int = 1024, temperature: float = 0.0,
                  schema_retries: int = 2) -> tuple[dict, dict]:
        """返回 `(parsed, raw_call)`；结构不合法时带纠错提示重问。"""
        current = prompt
        last_raw: dict | None = None
        errors: list[str] = []
        for _ in range(schema_retries + 1):
            raw = self.chat(current, system=system, max_tokens=max_tokens,
                            temperature=temperature)
            last_raw = raw
            try:
                return extract_json_object(raw["content"]), raw
            except LLMError as exc:
                errors.append(str(exc))
                current = (
                    "Your previous response was not valid JSON. Return ONLY one JSON "
                    "object matching the required schema, no prose.\n\n"
                    f"Original task:\n{prompt}\n\nPrevious response:\n{raw['content']}"
                )
        raise LLMError("JSON 解析失败：" + "; ".join(errors))
