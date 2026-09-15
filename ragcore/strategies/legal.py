"""法律域策略 — 从 document_service / rag_service 剥离的条文特化逻辑。

- LegalSplitStrategy：条文感知切分（≥3 个「第X条」标记启用，回退递归切分）
- LegalRetrievalStrategy：条文号精确匹配 + 锚点词匹配 + 向量召回
"""
import re
from langchain_core.documents import Document
from ragcore.config.config import ARTICLE_MAX_CHARS
from ragcore.strategies.base import SplitStrategy, RetrievalStrategy

SPLIT_ART_RE = re.compile(r"第[一二三四五六七八九十百千零0-9]+条")

RETRIEVAL_ART_RE = re.compile(r"第\s*([0-9]+|[一二三四五六七八九十百千零]+)\s*条")

LAW_KEYWORDS = [
    "劳动合同法", "消费者权益保护法", "个人信息保护法", "食品安全法", "未成年人保护法",
    "反电信网络诈骗法", "国家赔偿法", "行政强制法", "行政许可法", "行政复议法",
    "妇女权益保障法", "社会保险法", "个人所得税法", "道路交通安全法", "行政处罚法",
    "民法典", "公司法", "劳动法", "刑法", "居住证",
]

STOP_CHARS = set(
    "的了吗呢啊吧哪哟在了对为和是与及或这其之等也也都就还被由向从到给让把将并但而若如那"
    "个月年日人有些什么怎么如何规定需要应当可以我们国你请问关于根据最长是不得超过几上中下前后"
)
GENERIC_PREFIX = ["公司", "法律", "我国", "我们", "本", "该", "这个", "那个"]


def _extract_title(text):
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return ""


class LegalSplitStrategy(SplitStrategy):
    """条文感知切分。文档含 ≥3 个「第X条」标记时按条文边界切分，否则回退递归切分。"""

    def split(self, documents: list) -> list:
        split_docs = []
        for doc in documents:
            text = doc.page_content
            source = doc.metadata.get("source", "")
            title = _extract_title(text)
            matches = list(SPLIT_ART_RE.finditer(text))
            if len(matches) >= 3:
                segments = self._split_by_articles(text, title, matches)
            else:
                segments = self._recursive_split(text, title)
            for seg in segments:
                if seg.strip():
                    split_docs.append(Document(page_content=seg, metadata={"source": source}))
        return split_docs

    def _split_by_articles(self, text, title, matches):
        starts = [m.start() for m in matches]
        raw_segments = []
        if starts[0] > 0:
            raw_segments.append(text[:starts[0]])
        for i, s in enumerate(starts):
            e = starts[i + 1] if i + 1 < len(starts) else len(text)
            raw_segments.append(text[s:e])

        out = []
        for idx, seg in enumerate(raw_segments):
            body = (f"{title}\n{seg}" if title and idx > 0 else seg)
            out.extend(self._fit_window(body))
        return out

    def _recursive_split(self, text, title):
        body = f"{title}\n{text}" if title else text
        from langchain_text_splitters import RecursiveCharacterTextSplitter
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=ARTICLE_MAX_CHARS,
            chunk_overlap=max(60, ARTICLE_MAX_CHARS // 8),
            length_function=len,
        )
        return splitter.split_text(body)

    def _fit_window(self, text):
        if len(text) <= ARTICLE_MAX_CHARS:
            return [text]
        seps = re.compile(r"(?<=[。；;；\n])")
        pieces = [p for p in seps.split(text) if p]
        out, buf = [], ""
        for p in pieces:
            if len(buf) + len(p) <= ARTICLE_MAX_CHARS:
                buf += p
            else:
                if buf:
                    out.append(buf)
                if len(p) > ARTICLE_MAX_CHARS:
                    step = max(ARTICLE_MAX_CHARS - 80, 200)
                    for j in range(0, len(p), step):
                        out.append(p[j:j + step])
                    buf = ""
                else:
                    buf = p
        if buf:
            out.append(buf)
        return [o for o in out if o.strip()]


def _num_to_cn(num):
    if isinstance(num, str):
        if any(c in "一二三四五六七八九十百千零" for c in num):
            return num
        num = int(num)
    digits = "零一二三四五六七八九"
    if num == 0:
        return "零"
    s = str(num)
    length = len(s)
    res = ""
    for i, ch in enumerate(s):
        digit = int(ch)
        pos = length - i - 1
        if digit == 0:
            if not res.endswith("零") and i != length - 1:
                res += "零"
        else:
            unit = "十" if pos == 1 else "百" if pos == 2 else "千" if pos == 3 else ""
            res += digits[digit] + unit
    return res.rstrip("零")


def _parse_article(query):
    m = RETRIEVAL_ART_RE.search(query)
    if not m:
        return None, None
    num = m.group(1)
    law = None
    for kw in LAW_KEYWORDS:
        if kw in query:
            law = kw
            break
    return law, num


def _extract_key_anchors(query):
    q = query
    for kw in LAW_KEYWORDS:
        q = q.replace(kw, " ")
    anchors = set()
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", q):
        for g in GENERIC_PREFIX:
            run = run.replace(g, "")
        run = run.strip("".join(STOP_CHARS))
        if len(run) < 2:
            continue
        anchors.add(run[:2])
        anchors.add(run[-2:])
        for L in (3, 4):
            for i in range(len(run) - L + 1):
                sub = run[i:i + L]
                if len(sub) == L:
                    anchors.add(sub)
    return {a for a in anchors if len(a) >= 2 and not all(c in STOP_CHARS for c in a)}


class LegalRetrievalStrategy(RetrievalStrategy):
    """法律域检索：向量召回 + 条文号精确匹配 + 锚点词匹配。

    enable_article_match / enable_anchor_match 开关用于消融实验，
    关闭后仅保留向量召回，可验证各检索通道的独立贡献。
    """

    def __init__(self, enable_article_match=True, enable_anchor_match=True):
        self.enable_article_match = enable_article_match
        self.enable_anchor_match = enable_anchor_match

    def retrieve(self, query: str, vector_store, pool_size: int,
                 payload_filter: dict | None = None) -> dict:
        pool = vector_store.search_documents(query, k=pool_size, payload_filter=payload_filter)
        law, num = _parse_article(query)

        extra_docs, extra_metas = [], []
        seen = set()

        def _add(matches, cap=6):
            for m in matches:
                key = m["document"]
                if key not in seen:
                    seen.add(key)
                    extra_docs.append(key)
                    extra_metas.append(m["metadata"])
                    if len(extra_docs) >= cap:
                        return

        if num is not None and self.enable_article_match:
            target = "第" + _num_to_cn(num) + "条"
            _add(vector_store.search_by_keyword(target, source_filter=law))

        if self.enable_anchor_match:
            anchors = _extract_key_anchors(query)
            if anchors:
                _add(vector_store.search_by_anchors(anchors, source_filter=law))

        pool_docs = pool["documents"][0] if pool["documents"] else []
        pool_metas = pool["metadatas"][0] if pool.get("metadatas") else []
        pool_dist = pool["distances"][0] if pool.get("distances") else []

        seen2 = set(extra_docs)
        p_docs, p_metas, p_dist = [], [], []
        for d, mt, dist in zip(pool_docs, pool_metas, pool_dist):
            if d not in seen2:
                seen2.add(d)
                p_docs.append(d)
                p_metas.append(mt)
                p_dist.append(dist)

        docs = extra_docs + p_docs
        metas = extra_metas + p_metas
        dists = [0.0] * len(extra_docs) + p_dist
        return {
            "documents": [docs],
            "metadatas": [metas],
            "distances": [dists],
        }
