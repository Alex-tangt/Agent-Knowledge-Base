"""代目录 + 指针：派生索引的原子替换布局（issue #13 / ADR-0011）。

布局：

    INDEX_DIR/
      CURRENT                 # 指针文件，内容是一代的名字（如 gen-2）
      gen-1/{qdrant/,manifest.json}
      gen-2/{qdrant/,manifest.json}

为什么是代目录：Qdrant local mode 的锁按**目录**持有（ADR-0008 D5），不同代是
不同目录 = 不同锁，重建期间旧代照常被搜索，互不打断。指针切换用 `os.replace`，
对读者原子可见。中断只留下一个未接管的代目录，不破坏旧代。
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile

from memory_agent.settings import INDEX_DIR, POINTER_NAME

_GEN_RE = re.compile(r"^gen-(\d+)$")


class IndexLayout:
    def __init__(self, root: str | None = None, pointer_name: str = POINTER_NAME):
        self.root = os.path.abspath(root or INDEX_DIR)
        self._pointer_name = pointer_name

    # --------------------------------------------------------------- pointer

    @property
    def pointer_path(self) -> str:
        return os.path.join(self.root, self._pointer_name)

    def read_pointer(self) -> str | None:
        """读当前代名；文件缺失 / 内容非法时返回 None（视为未构建）。"""
        try:
            with open(self.pointer_path, "r", encoding="utf-8") as handle:
                gen = handle.read().strip()
        except OSError:
            return None
        return gen if _GEN_RE.match(gen) else None

    def write_pointer(self, gen: str) -> None:
        """原子切换指针：写临时文件后 os.replace（读者要么看到旧代、要么看到新代）。"""
        if not _GEN_RE.match(gen):
            raise ValueError(f"非法代名：{gen!r}")
        os.makedirs(self.root, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", suffix=".tmp", dir=self.root, delete=False,
        )
        try:
            handle.write(gen + "\n")
            handle.close()
            os.replace(handle.name, self.pointer_path)
        except BaseException:
            handle.close()
            try:
                os.remove(handle.name)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------ generation

    def gen_dir(self, gen: str) -> str:
        return os.path.join(self.root, gen)

    def db_path(self, gen: str) -> str:
        return os.path.join(self.gen_dir(gen), "qdrant")

    def manifest_path(self, gen: str) -> str:
        return os.path.join(self.gen_dir(gen), "manifest.json")

    def plan_path(self, gen: str) -> str:
        return os.path.join(self.gen_dir(gen), "plan.json")

    def generations(self) -> list[str]:
        """已有代，按序号升序。"""
        if not os.path.isdir(self.root):
            return []
        found = [
            name for name in os.listdir(self.root)
            if _GEN_RE.match(name) and os.path.isdir(os.path.join(self.root, name))
        ]
        return sorted(found, key=lambda name: int(_GEN_RE.match(name).group(1)))

    def next_gen(self) -> str:
        """下一个未占用的代名（即使没有指针、只有未接管的残留代，也不会撞名）。"""
        numbers = [int(_GEN_RE.match(name).group(1)) for name in self.generations()]
        return f"gen-{max(numbers, default=0) + 1}"

    def resolve(self) -> tuple[str, str, str] | None:
        """当前代的 (gen, db_path, manifest_path)；未构建返回 None。"""
        gen = self.read_pointer()
        if gen is None:
            return None
        return gen, self.db_path(gen), self.manifest_path(gen)

    def prune(self, keep: int = 2) -> list[str]:
        """保留最新的 keep 代（含当前），删更旧的；best-effort，删不掉不报错。

        keep>=2 是为了给可能正在旧代上检索的进程留缓冲（跨进程无协调）。
        """
        generations = self.generations()
        if len(generations) <= keep:
            return []
        stale = generations[: len(generations) - keep]
        for gen in stale:
            shutil.rmtree(self.gen_dir(gen), ignore_errors=True)
        return stale
