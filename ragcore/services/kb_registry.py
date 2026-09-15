import json
import os
from ragcore.config.config import LEGAL_WEB_DIR
from ragcore.utils.logger import logger

KB_REGISTRY_FILE = os.path.join(LEGAL_WEB_DIR, "kb_registry.json")


def _load():
    if not os.path.exists(KB_REGISTRY_FILE):
        default = {
            "documents": {
                "name": "documents",
                "label": "政策法规知识库",
                "description": "中国政策法规文档",
                "split_strategy": "legal",
                "retrieval_strategy": "legal",
            }
        }
        _save(default)
        return default
    with open(KB_REGISTRY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(registry):
    with open(KB_REGISTRY_FILE, "w", encoding="utf-8") as f:
        json.dump(registry, f, ensure_ascii=False, indent=2)


class KBRegistry:
    def list(self):
        reg = _load()
        return [{"name": k, **v} for k, v in reg.items()]

    def get(self, name):
        reg = _load()
        return reg.get(name)

    def create(self, name, label, description="", split_strategy="default", retrieval_strategy="default"):
        reg = _load()
        if name in reg:
            raise ValueError(f"知识库 '{name}' 已存在")
        reg[name] = {
            "name": name,
            "label": label,
            "description": description,
            "split_strategy": split_strategy,
            "retrieval_strategy": retrieval_strategy,
        }
        _save(reg)
        logger.info(f"Created KB: {name} ({label}) strategies=split:{split_strategy}/retrieval:{retrieval_strategy}")
        return reg[name]

    def delete(self, name):
        reg = _load()
        if name not in reg:
            raise ValueError(f"知识库 '{name}' 不存在")
        if name == "documents":
            raise ValueError("不能删除默认知识库")
        del reg[name]
        _save(reg)
        logger.info(f"Deleted KB: {name}")

    def default_name(self):
        return "documents"


kb_registry = KBRegistry()
