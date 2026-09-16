import json
import os
from ragcore.config.config import LEGAL_WEB_DIR
from ragcore.utils.logger import logger

# 视图注册表（#37 命名迁移：原 kb_registry.json）。视图 = 具名读谓词（只读），
# 最终可见 = 视图 ∩ 身份授权；词汇见 CONTEXT.md / ADR-0025 D4。
VIEW_REGISTRY_FILE = os.path.join(LEGAL_WEB_DIR, "view_registry.json")

# 旧文件名：仅在读不到新文件时用于一次性迁移，不写回。
LEGACY_REGISTRY_FILE = os.path.join(LEGAL_WEB_DIR, "kb_registry.json")


def _load():
    if not os.path.exists(VIEW_REGISTRY_FILE):
        if os.path.exists(LEGACY_REGISTRY_FILE):
            with open(LEGACY_REGISTRY_FILE, "r", encoding="utf-8") as f:
                registry = json.load(f)
            _save(registry)
            return registry
        default = {
            "documents": {
                "name": "documents",
                "label": "政策法规视图",
                "description": "中国政策法规文档",
                "split_strategy": "legal",
                "retrieval_strategy": "legal",
            }
        }
        _save(default)
        return default
    with open(VIEW_REGISTRY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(registry):
    with open(VIEW_REGISTRY_FILE, "w", encoding="utf-8") as f:
        json.dump(registry, f, ensure_ascii=False, indent=2)


class ViewRegistry:
    def list(self):
        reg = _load()
        return [{"name": k, **v} for k, v in reg.items()]

    def get(self, name):
        reg = _load()
        return reg.get(name)

    def create(self, name, label, description="", split_strategy="default", retrieval_strategy="default"):
        reg = _load()
        if name in reg:
            raise ValueError(f"视图 '{name}' 已存在")
        reg[name] = {
            "name": name,
            "label": label,
            "description": description,
            "split_strategy": split_strategy,
            "retrieval_strategy": retrieval_strategy,
        }
        _save(reg)
        logger.info(f"Created view: {name} ({label}) strategies=split:{split_strategy}/retrieval:{retrieval_strategy}")
        return reg[name]

    def delete(self, name):
        reg = _load()
        if name not in reg:
            raise ValueError(f"视图 '{name}' 不存在")
        if name == "documents":
            raise ValueError("不能删除默认视图")
        del reg[name]
        _save(reg)
        logger.info(f"Deleted view: {name}")

    def default_name(self):
        return "documents"


view_registry = ViewRegistry()
