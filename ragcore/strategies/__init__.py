"""策略注册与解析 — 按视图（view）配置加载切分/检索策略。

视图元数据中可通过 `split_strategy` / `retrieval_strategy` 字段指定策略名：
- legal：法律域策略（条文感知切分 + 条文/锚点混合检索）
- default：通用策略（递归切分 + 纯向量检索）
未指定时使用 default，保证通用工具开箱即用。
"""
from ragcore.strategies.base import SplitStrategy, RetrievalStrategy
from ragcore.strategies.legal import LegalSplitStrategy, LegalRetrievalStrategy
from ragcore.strategies.default import DefaultSplitStrategy, DefaultRetrievalStrategy

SPLIT_STRATEGIES = {
    "legal": LegalSplitStrategy,
    "default": DefaultSplitStrategy,
}

RETRIEVAL_STRATEGIES = {
    "legal": LegalRetrievalStrategy,
    "default": DefaultRetrievalStrategy,
}

DEFAULT_SPLIT = "default"
DEFAULT_RETRIEVAL = "default"


def _view_config(view_name):
    if not view_name:
        try:
            from ragcore.services.view_registry import view_registry
            view_name = view_registry.default_name()
        except Exception:
            pass
    if not view_name:
        return {}
    try:
        from ragcore.services.view_registry import view_registry
        return view_registry.get(view_name) or {}
    except Exception:
        return {}


def get_split_strategy(view_name=None) -> SplitStrategy:
    name = _view_config(view_name).get("split_strategy", DEFAULT_SPLIT)
    cls = SPLIT_STRATEGIES.get(name, DefaultSplitStrategy)
    return cls()


def get_retrieval_strategy(view_name=None) -> RetrievalStrategy:
    name = _view_config(view_name).get("retrieval_strategy", DEFAULT_RETRIEVAL)
    cls = RETRIEVAL_STRATEGIES.get(name, DefaultRetrievalStrategy)
    return cls()


def create_retrieval_strategy(name="default", **kwargs) -> RetrievalStrategy:
    cls = RETRIEVAL_STRATEGIES.get(name, DefaultRetrievalStrategy)
    return cls(**kwargs)


__all__ = [
    "SplitStrategy", "RetrievalStrategy",
    "LegalSplitStrategy", "LegalRetrievalStrategy",
    "DefaultSplitStrategy", "DefaultRetrievalStrategy",
    "get_split_strategy", "get_retrieval_strategy", "create_retrieval_strategy",
]
