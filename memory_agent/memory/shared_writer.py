"""共享域写入（issue #33 / ADR-0025 D16）：DB 即真相源，写路径**不外溢**本地机制。

本地域走 `writer.MemoryWriter`（Markdown 文件 + git commit + 代目录/指针由索引层管）；
共享域走这里——写入 = **DB upsert/delete**（Qdrant 按点原子），并发交给 DB，不做自定义
冲突处理，**没有 git / 代目录 / 指针切换 / 惰性重建**（那些是本地专用机制）。

知识语义（结构化校验 / frontmatter 渲染 / supersede-archive / 去重）**复用 app 层**：
`authoring`（渲染 + 校验）+ 端口的 dense 检索通道（去重）。

去重阈值用 **dense 余弦**（`store.search_dense`）：hybrid 的融合分（RRF/DBSF）量纲不同，
不能套余弦阈值（ADR-0019 D6 的"分数/阈值按平面"）。共享平面的条目 payload 里带
`residency=cloud`、`owner`、`tenant` 等镜像，供网关过滤与 provenance。
"""
from __future__ import annotations

import hashlib

from memory_agent.memory import authoring
from memory_agent.memory.entries import point_id_for
from memory_agent.memory.ports import DEFAULT_CLASSIFICATION, PLANE_CLOUD
from memory_agent.settings import DEDUP_THRESHOLD, MAX_ENTRY_CHARS

SUPERSEDABLE_STATUS = frozenset({"current", "draft"})
SHARED_SOURCE = "shared"


class SharedWriteError(RuntimeError):
    """共享域写入被拒或失败；消息面向调用方，含修正方向。"""


class SharedMemoryWriter:
    """共享域的写入网关：所有变更经 `store`（DB），不碰本地文件 / git / 指针。"""

    def __init__(self, store, *, owner: str | None = None, tenant: str | None = None,
                 tag_vocab: set[str] | None = None):
        self._store = store
        self.owner = owner
        self.tenant = tenant
        self._tag_vocab = tag_vocab

    # ------------------------------------------------------------------ add

    def add(self, *, title: str, body: str, domain: str, type: str, tags: list[str],
            slug: str | None = None, sources: list[str] | None = None,
            status: str = "current", allow_duplicate: bool = False,
            dedup_threshold: float = DEDUP_THRESHOLD,
            classification: str = DEFAULT_CLASSIFICATION, residency: str = "cloud",
            today: str | None = None, payload_filter: dict | None = None) -> dict:
        """写入一条共享域记忆（DB upsert）。返回 `{"status": "written"|"duplicate", ...}`。"""
        prepared = self._prepare(
            title=title, body=body, domain=domain, type=type, tags=tags, slug=slug,
            sources=sources, status=status, today=today,
            classification=classification, residency=residency,
        )
        entry_id = prepared["id"]

        if self._exists(entry_id):
            return self._duplicate(entry_id, prepared["title"], reason="exists",
                                   hint="已存在同 id 条目：如需替换，改用 memory_supersede（#12）")

        if not allow_duplicate:
            duplicates = self._find_duplicates(
                prepared["embedding_text"], dedup_threshold, payload_filter)
            if duplicates:
                return self._duplicate(
                    entry_id, prepared["title"], reason="semantic", candidates=duplicates,
                    hint="已有近似条目：改用 memory_supersede（#12），"
                         "或确认确实不同后以 allow_duplicate=true 重试")

        self._store.add([prepared["embedding_text"]], metadata_list=[prepared["payload"]],
                        ids=[point_id_for(entry_id)])
        return {
            "status": "written",
            "written": True,
            "action": "add",
            "id": entry_id,
            "plane": PLANE_CLOUD,
            "warnings": prepared["warnings"],
            "index": self._index_status(),
        }

    # ------------------------------------------------------------- archive

    def archive(self, *, entry_id: str, reason: str, confirm: bool = False,
                today: str | None = None) -> dict:
        """把共享条目退役（payload `status: archived` + `archive_reason`），点保留。"""
        reason = (reason or "").strip()
        if not reason:
            raise SharedWriteError("reason 不能为空：归档必须留下为什么")
        payload = self._read(entry_id)
        if payload.get("status") == "archived":
            raise SharedWriteError(f"条目 {entry_id} 已经归档过了")
        preview = {
            "entry": {"id": entry_id, "title": payload.get("title"),
                      "status": payload.get("status")},
            "changes": {"status": "archived", "archive_reason": reason,
                        "updated": today or authoring.today_str()},
            "scope": "db-only（无 git / 无文件 / 无指针）",
        }
        if not confirm:
            return {"status": "confirmation_required", "written": False,
                    "action": "archive", "preview": preview,
                    "hint": "破坏性变更（条目退役，点保留）：确认后以 confirm=true 重试。"}
        updated = dict(payload)
        updated.update({"status": "archived", "archive_reason": reason,
                        "updated": today or authoring.today_str()})
        self._store.add([updated.get("text") or updated.get("title", "")],
                        metadata_list=[updated], ids=[point_id_for(entry_id)])
        return {"status": "written", "written": True, "action": "archive",
                "id": entry_id, "preview": preview, "index": self._index_status()}

    # ----------------------------------------------------------- supersede

    def supersede(self, *, old_id: str, title: str, body: str, domain: str, type: str,
                  tags: list[str], slug: str | None = None, sources: list[str] | None = None,
                  classification: str = DEFAULT_CLASSIFICATION, residency: str = "cloud",
                  confirm: bool = False, today: str | None = None) -> dict:
        """新条目替代旧条目（DB）：新点 upsert + 旧点 payload 标注退役（同一次操作）。"""
        old_payload = self._read(old_id)
        if old_payload.get("status") not in SUPERSEDABLE_STATUS:
            raise SharedWriteError(
                f"旧条目 {old_id} 状态为 {old_payload.get('status')!r}："
                f"只有 {sorted(SUPERSEDABLE_STATUS)} 才能被替代")
        prepared = self._prepare(
            title=title, body=body, domain=domain, type=type, tags=tags, slug=slug,
            sources=sources, status="current", today=today,
            classification=classification, residency=residency, extra={"supersedes": old_id},
        )
        if self._exists(prepared["id"]):
            raise SharedWriteError(f"目标条目已存在：{prepared['id']}（换一个 slug）")

        preview = {
            "old": {"id": old_id, "title": old_payload.get("title"),
                    "status": old_payload.get("status")},
            "new": {"id": prepared["id"], "title": prepared["title"], "domain": domain},
            "scope": "db-only（无 git / 无文件 / 无指针）",
        }
        if not confirm:
            return {"status": "confirmation_required", "written": False,
                    "action": "supersede", "preview": preview,
                    "hint": "破坏性变更（旧条目退役）：确认后以 confirm=true 重试。"}

        updated_old = dict(old_payload)
        updated_old.update({"status": "superseded", "superseded_by": prepared["id"],
                            "updated": today or authoring.today_str()})
        self._store.add([prepared["embedding_text"]], metadata_list=[prepared["payload"]],
                        ids=[point_id_for(prepared["id"])])
        self._store.add([updated_old.get("text") or updated_old.get("title", "")],
                        metadata_list=[updated_old], ids=[point_id_for(old_id)])
        return {"status": "written", "written": True, "action": "supersede",
                "old_id": old_id, "new_id": prepared["id"], "id": prepared["id"],
                "preview": preview, "warnings": prepared["warnings"],
                "index": self._index_status()}

    # ------------------------------------------------------------- internals

    def _prepare(self, *, title, body, domain, type, tags, slug, sources, status, today,
                 classification, residency, extra=None) -> dict:
        title = (title or "").strip()
        body = (body or "").strip()
        if not title or "\n" in title:
            raise SharedWriteError("title 必须是非空单行文本")
        if not body:
            raise SharedWriteError("body 不能为空")
        tags = [str(tag).strip() for tag in (tags or []) if str(tag).strip()]
        if not tags:
            raise SharedWriteError("tags 不能为空（从 tags.md 的受控表里取）")
        try:
            domain = authoring.normalize_domain(domain)
        except ValueError as exc:
            raise SharedWriteError(str(exc)) from exc
        if type not in authoring.VALID_TYPES or type not in authoring.allowed_types(domain):
            raise SharedWriteError(
                f"{domain} 下 type 必须是 {sorted(authoring.allowed_types(domain))}，收到 {type!r}")
        if status not in authoring.VALID_STATUS:
            raise SharedWriteError(f"status {status!r} 不合法")

        resolved_slug = (slug or authoring.slugify(title)).strip()
        if not authoring.SLUG_RE.match(resolved_slug):
            raise SharedWriteError(
                f"slug {resolved_slug!r} 不合法：中文标题无法生成英文 slug 时请显式传 slug")
        entry_id = f"{domain}/{resolved_slug}"
        today = today or authoring.today_str()
        text = authoring.render_entry(
            entry_id=entry_id, title=title, type=type, tags=tags, status=status,
            updated=today, sources=sources or [], body=body, extra=extra)
        errors, warnings = authoring.validate_entry(text, f"{entry_id}.md", self._tag_vocab)
        if errors:
            raise SharedWriteError("条目校验失败：\n  " + "\n  ".join(errors))

        embedding_text = f"{title}\n\n{body}"[:MAX_ENTRY_CHARS]
        payload = {
            "entry_id": entry_id,
            "title": title,
            "source": SHARED_SOURCE,
            "writable": True,
            "type": type,
            "tags": tags,
            "status": status,
            "updated": today,
            "domain": domain,
            "owner": self.owner,
            "tenant": self.tenant,
            "classification": classification,
            "residency": residency,
            "content": text,
            "hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "text": embedding_text,
        }
        payload.update(extra or {})
        return {"id": entry_id, "title": title, "domain": domain,
                "embedding_text": embedding_text, "payload": payload, "warnings": warnings}

    def _exists(self, entry_id: str) -> bool:
        return bool(self._store.fetch([point_id_for(entry_id)]))

    def _read(self, entry_id: str) -> dict:
        records = self._store.fetch([point_id_for(entry_id)])
        if not records:
            raise SharedWriteError(f"未知条目 id：{entry_id}（先用 memory_search 取 id）")
        return records[0]

    def _find_duplicates(self, query_text: str, threshold: float,
                         payload_filter: dict | None) -> list[dict]:
        scoped = dict(payload_filter or {})
        scoped["writable"] = True
        result = self._store.search_dense(query_text, k=5, payload_filter=scoped)
        docs = result["documents"][0] if result.get("documents") else []
        metas = result["metadatas"][0] if result.get("metadatas") else []
        dists = result["distances"][0] if result.get("distances") else []
        keys = ("entry_id", "title", "source", "status")
        out = []
        for doc, meta, dist in zip(docs, metas, dists):
            if float(dist) < threshold:
                continue
            item = {key: meta.get(key) for key in keys}
            item["id"] = meta.get("entry_id")
            item["score"] = float(dist)
            out.append(item)
        return out

    def _duplicate(self, entry_id, title, *, reason, candidates=None, hint=None) -> dict:
        return {
            "status": "duplicate",
            "written": False,
            "reason": reason,
            "id": entry_id,
            "candidates": candidates or [
                {"id": entry_id, "title": title, "score": None, "reason": "同 id 已存在"}],
            "hint": hint,
        }

    def _index_status(self) -> dict:
        """共享域写入即落 DB（索引就是 DB 本身），无本地代目录 / 指针 / 惰性刷。"""
        return {"ok": True, "refreshed": True, "mode": "db",
                "reason": "共享域以 DB 为真相源，写入即索引（D16）"}


__all__ = ["SharedMemoryWriter", "SharedWriteError"]
