"""LangGraph 智能路由 Agent — 意图识别 + KB 分流"""
import json
from langgraph.graph import StateGraph, END
from typing import TypedDict, List, Optional
from config.llm import require_llm
from utils.logger import logger
import openai

SYSTEM_PROMPT = """你是一个知识库路由专家。根据用户问题，判断应该使用哪个知识库来查找答案。
只返回 JSON 格式：{"kb_name": "知识库名称", "reason": "简短理由"}
知识库列表：{kb_list}
如果无法匹配任何知识库，返回 kb_name 为 null。
只返回 JSON，不要其他内容。"""


class RouterState(TypedDict):
    query: str
    kb_list: List[dict]
    kb_name: Optional[str]
    confidence: Optional[str]


class RouterAgent:
    def __init__(self):
        llm = require_llm()
        self.model = llm.model
        self.client = openai.AsyncOpenAI(
            api_key=llm.api_key,
            base_url=llm.base_url,
        )

    async def classify(self, query: str, kb_list: List[dict]) -> dict:
        if len(kb_list) <= 1:
            name = kb_list[0]["name"] if kb_list else "documents"
            return {"kb_name": name, "reason": "唯一知识库"}

        kb_desc = "\n".join(
            f"- {kb['name']}: {kb.get('label', kb['name'])} ({kb.get('description', '')})"
            for kb in kb_list
        )
        prompt = SYSTEM_PROMPT.format(kb_list=kb_desc)

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": query},
                ],
                temperature=0,
                max_tokens=80,
            )
            content = response.choices[0].message.content.strip()
            content = content.replace("```json", "").replace("```", "").strip()
            result = json.loads(content)
            logger.info(f"Router classified '{query[:40]}...' -> {result.get('kb_name')}")
            return result
        except Exception as e:
            logger.warning(f"Router classification failed: {e}")
            return {"kb_name": None, "reason": "分类失败"}


_router_instance = None


def get_router() -> RouterAgent:
    global _router_instance
    if _router_instance is None:
        _router_instance = RouterAgent()
    return _router_instance


def build_router_graph():
    builder = StateGraph(RouterState)

    async def classify_node(state: RouterState) -> RouterState:
        router = get_router()
        result = await router.classify(state["query"], state["kb_list"])
        state["kb_name"] = result.get("kb_name")
        state["confidence"] = result.get("reason", "")
        return state

    builder.add_node("classify", classify_node)
    builder.set_entry_point("classify")
    builder.add_edge("classify", END)

    return builder.compile()
