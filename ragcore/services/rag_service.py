import openai
import re
import time
import json
import os
from config.config import (
    API_KEY, BASE_URL, MODEL, ADAPTIVE_POOL, ADAPTIVE_MAX,
    ADAPTIVE_FACTOR, RELEVANCE_THRESHOLD,
    USE_LOCAL_RERANKER, LOCAL_RERANKER_MODEL,
    QDRANT_COLLECTION_NAME,
)
from services.vector_store_service import VectorStoreService
from services.langsmith_service import langsmith_service
from strategies import get_retrieval_strategy
from utils.logger import logger
from utils.model_status import STATUS

NO_EVIDENCE_MESSAGE = "知识库中未找到直接依据，建议提供更具体的问题或补充相关资料。"

PROMPT_NO_EVIDENCE = (
    "若上下文完全不涉及用户问题，或内容完全不相关，才回复：" + NO_EVIDENCE_MESSAGE
)


class RAGService:
    def __init__(self):
        try:
            self.client = openai.AsyncOpenAI(
                api_key=API_KEY,
                base_url=BASE_URL,
            )

            self._vector_stores = {}
            self._default_kb = QDRANT_COLLECTION_NAME
            self.get_vector_store(self._default_kb)

            self._reranker = None

            logger.info("RAGService initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize RAGService: {e}")
            raise

    def get_vector_store(self, kb_name):
        if kb_name not in self._vector_stores:
            self._vector_stores[kb_name] = VectorStoreService(collection_name=kb_name)
        return self._vector_stores[kb_name]

    @property
    def vector_store(self):
        return self._vector_stores[self._default_kb]

    @property
    def reranker(self):
        if self._reranker is None and USE_LOCAL_RERANKER:
            STATUS["reranker"] = "loading"
            logger.info("Loading reranker model...")
            from services.reranker_service import RerankerService
            self._reranker = RerankerService(LOCAL_RERANKER_MODEL)
            STATUS["reranker"] = "ready"
            logger.info("Reranker model ready")
        return self._reranker

    async def _route_query(self, query, kb_name=None):
        if kb_name and kb_name != "auto":
            return kb_name
        try:
            from services.kb_registry import kb_registry
            from agents.router_graph import get_router
            kb_list = kb_registry.list()
            if len(kb_list) <= 1:
                return kb_list[0]["name"] if kb_list else self._default_kb
            result = await get_router().classify(query, kb_list)
            routed = result.get("kb_name") or self._default_kb
            logger.info(f"Auto-routed -> {routed}: {result.get('reason', '')}")
            return routed
        except Exception as e:
            logger.warning(f"Auto-routing failed, using default: {e}")
            return self._default_kb

    def warmup(self):
        self.vector_store.warmup()
        if USE_LOCAL_RERANKER:
            self.reranker
        STATUS["ready"] = True
        logger.info("All models loaded, system ready")

    @langsmith_service.trace(name="rag_build_prompt", metadata={"service": "RAGService"})
    def build_rag_prompt(self, query, retrieved):
        try:
            docs = retrieved["documents"][0] if retrieved["documents"] else []
            metadatas = retrieved["metadatas"][0] if retrieved.get("metadatas") else []

            context = ""
            for i, doc in enumerate(docs):
                source = metadatas[i].get("source", f"文档{i+1}") if i < len(metadatas) else f"文档{i+1}"
                source = os.path.basename(str(source))
                context += f"[{i+1}] 来源：{source}\n{doc}\n\n"

            prompt = f"""请严格依据以下上下文回答用户关于政策法规的问题。

要求：
1. 优先从上下文中提取相关信息作答，使用 [n] 标注来源编号（例如 [1]）。
2. 若上下文部分相关但不完整：说明已知信息，并提示"此处未涵盖完整规定，建议查阅原文"。
3. {PROMPT_NO_EVIDENCE}
4. 回答末尾注明：（仅供学习参考，不构成法律意见）

Context:
{context}

Question:
{query}

Answer:
"""
            return prompt
        except Exception as e:
            logger.error(f"Error building RAG prompt: {e}")
            raise

    def _extract_sources(self, retrieved):
        docs = retrieved["documents"][0] if retrieved["documents"] else []
        metadatas = retrieved["metadatas"][0] if retrieved.get("metadatas") else []
        distances = retrieved["distances"][0] if retrieved.get("distances") else []

        sources = []
        for i, doc in enumerate(docs):
            meta = metadatas[i] if i < len(metadatas) else {}
            sources.append({
                "content": doc,
                "source": os.path.basename(str(meta.get("source", f"文档{i+1}"))),
                "score": round(float(distances[i]), 4) if i < len(distances) else None,
                "chunk_id": i + 1,
            })
        return sources

    def _has_evidence(self, retrieved):
        docs = retrieved["documents"][0] if retrieved["documents"] else []
        if not docs:
            return False
        if RELEVANCE_THRESHOLD is not None:
            distances = retrieved["distances"][0] if retrieved.get("distances") else []
            if distances and min(distances) > RELEVANCE_THRESHOLD:
                return False
        return True

    def _select_adaptive(self, retrieved):
        docs = retrieved.get("documents", [[]])[0]
        metadatas = retrieved.get("metadatas", [[]])[0]
        distances = retrieved.get("distances", [[]])[0]
        if not docs or not distances:
            return retrieved

        best = distances[0]
        selected = []
        for i, d in enumerate(distances):
            if d <= best * ADAPTIVE_FACTOR and len(selected) < ADAPTIVE_MAX:
                selected.append(i)
            else:
                break

        if not selected:
            selected = [0]

        return {
            "documents": [[docs[i] for i in selected]],
            "metadatas": [[metadatas[i] for i in selected]],
            "distances": [[distances[i] for i in selected]],
        }

    def _rerank(self, query, retrieved):
        if not self.reranker:
            return retrieved

        docs = retrieved["documents"][0] if retrieved["documents"] else []
        metadatas = retrieved["metadatas"][0] if retrieved.get("metadatas") else []

        if not docs:
            return retrieved

        try:
            ranked = self.reranker.rerank(query, docs, top_k=len(docs))
        except Exception as e:
            logger.warning(f"Reranker failed, falling back to vector scores: {e}")
            return retrieved

        reranked_docs = []
        reranked_metas = []
        reranked_dists = []
        for score, doc in ranked:
            reranked_docs.append(doc)
            idx = docs.index(doc) if doc in docs else 0
            reranked_metas.append(metadatas[idx] if idx < len(metadatas) else {})
            reranked_dists.append(round(1.0 - float(score), 4))

        return {
            "documents": [reranked_docs],
            "metadatas": [reranked_metas],
            "distances": [reranked_dists],
        }

    def _no_evidence_chunk(self):
        return json.dumps({
            "type": "content",
            "content": NO_EVIDENCE_MESSAGE,
            "sources": []
        }) + "\n"

    async def _rewrite_query(self, query: str, enable_multi: bool = False, rewrite_prompt: str = None) -> list[str]:
        try:
            if enable_multi:
                system_prompt = rewrite_prompt or "将用户问题转化为搜索关键词。如问题较复杂，可分解为多条简洁的搜索短语，每行一条。最多5条。"
                max_tokens_val = 300
            else:
                system_prompt = "将用户问题压缩为适合法律文献检索的关键词短语，去除语气词和多余描述，保留法律概念和核心诉求。仅输出改写后的查询，不要解释。"
                max_tokens_val = 60

            response = await self.client.chat.completions.create(
                model=MODEL,
                messages=[{
                    "role": "system",
                    "content": system_prompt
                }, {
                    "role": "user",
                    "content": query
                }],
                temperature=0,
                max_tokens=max_tokens_val,
            )
            content = response.choices[0].message.content.strip()

            if enable_multi:
                lines = [line.strip() for line in content.split("\n") if line.strip()]
                cleaned_lines = []
                for line in lines:
                    cleaned = re.sub(r"^[\d]+[\.\、\))\s]+", "", line).strip()
                    if cleaned:
                        cleaned_lines.append(cleaned)
                lines = cleaned_lines[:5]
                if lines:
                    logger.info(f"Query rewritten (multi): '{query}' -> {lines}")
                    return lines
            else:
                if content and len(content) > 3:
                    logger.info(f"Query rewritten: '{query}' -> '{content}'")
                    return [content]
        except Exception as e:
            logger.warning(f"Query rewrite failed: {e}")
        return [query]

    def _hybrid_retrieve(self, query, kb_name=None):
        if isinstance(query, list):
            if len(query) == 1:
                return self._hybrid_retrieve_single(query[0], kb_name)
            return self._hybrid_retrieve_multi(query, kb_name)
        return self._hybrid_retrieve_single(query, kb_name)

    def _hybrid_retrieve_single(self, query, kb_name=None):
        vs = self.get_vector_store(kb_name or self._default_kb)
        strategy = get_retrieval_strategy(kb_name or self._default_kb)
        return strategy.retrieve(query, vs, pool_size=ADAPTIVE_POOL)

    def _hybrid_retrieve_multi(self, queries, kb_name=None):
        vs = self.get_vector_store(kb_name or self._default_kb)
        strategy = get_retrieval_strategy(kb_name or self._default_kb)
        all_docs, all_metas, all_dists = [], [], []
        seen = set()
        for sq in queries:
            result = strategy.retrieve(sq, vs, pool_size=ADAPTIVE_POOL)
            docs = result["documents"][0] if result.get("documents") else []
            metas = result["metadatas"][0] if result.get("metadatas") else []
            dists = result["distances"][0] if result.get("distances") else []
            for d, mt, dist in zip(docs, metas, dists):
                if d not in seen:
                    seen.add(d)
                    all_docs.append(d)
                    all_metas.append(mt)
                    all_dists.append(dist)
        return {
            "documents": [all_docs],
            "metadatas": [all_metas],
            "distances": [all_dists],
        }

    async def rag_chat_stream(self, messages, kb_name=None, session_id=""):
        try:
            user_message = messages[-1]["content"]
            logger.info(f"Received RAG chat request: {user_message}")

            if session_id:
                from agents.session_memory import session_memory
                session_memory.add(session_id, "user", user_message)

            with langsmith_service.trace_context(
                name="rag_chat_stream",
                metadata={
                    "service": "RAGService",
                    "message_count": len(messages),
                    "model": MODEL
                }
            ):
                t_start = time.time()

                target_kb = await self._route_query(user_message, kb_name)
                search_query = await self._rewrite_query(user_message)
                t_after_rewrite = time.time()

                retrieved_docs = self._hybrid_retrieve(search_query, kb_name=target_kb)
                t_after_retrieve = time.time()

                retrieved_docs = self._rerank(user_message, retrieved_docs)
                t_after_rerank = time.time()

                if not self._has_evidence(retrieved_docs):
                    logger.info("未检索到可靠依据，返回无依据拒答")
                    t_end = time.time()
                    yield self._no_evidence_chunk()
                    yield json.dumps({
                        "type": "metadata",
                        "thinking_time": round(t_end - t_start, 2),
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "total_tokens": 0,
                        "sources": [],
                        "timing": {
                            "rewrite_ms": round((t_after_rewrite - t_start) * 1000, 1),
                            "retrieve_ms": round((t_after_retrieve - t_after_rewrite) * 1000, 1),
                            "rerank_ms": round((t_after_rerank - t_after_retrieve) * 1000, 1),
                            "generate_ms": round((t_end - t_after_rerank) * 1000, 1),
                        }
                    }) + "\n"
                    return

                retrieved_docs = self._select_adaptive(retrieved_docs)

                sources = self._extract_sources(retrieved_docs)
                rag_prompt = self.build_rag_prompt(user_message, retrieved_docs)

                rag_messages = [
                    {
                        "role": "system",
                        "content": (
                            "你是一个政策法规问答助手。请依据提供的上下文回答用户问题，"
                            "使用 [n] 标注引用来源。若上下文中包含相关信息但不完整，"
                            "可以基于已知内容给出分析，同时说明局限性。"
                            "仅当上下文完全不涉及问题时才说明无法回答。"
                            "回答末尾注明" + "\uff08仅供学习参考，不构成法律意见\uff09\u3002"
                        )
                    },
                    {"role": "user", "content": rag_prompt}
                ]

                response = await self.client.chat.completions.create(
                    model=MODEL,
                    messages=rag_messages,
                    temperature=0,
                    stream=True,
                )

                full_content = ""
                usage = None
                chunk_count = 0

                async for chunk in response:
                    chunk_count += 1
                    if chunk.choices[0].delta.content:
                        content = chunk.choices[0].delta.content
                        full_content += content
                        yield json.dumps({
                            "type": "content",
                            "content": content,
                            "sources": sources
                        }) + "\n"
                    if hasattr(chunk, 'usage') and chunk.usage:
                        usage = chunk.usage

                t_end = time.time()
                thinking_time = round(t_end - t_start, 2)

                if usage:
                    input_tokens = usage.prompt_tokens
                    output_tokens = usage.completion_tokens
                    total_tokens = usage.total_tokens
                    logger.info(f"API usage: prompt_tokens={input_tokens}, completion_tokens={output_tokens}, total_tokens={total_tokens}")
                else:
                    input_tokens = len(rag_prompt.split())
                    output_tokens = len(full_content.split())
                    total_tokens = input_tokens + output_tokens
                    logger.warning("API usage not available, using estimated values")

                yield json.dumps({
                    "type": "metadata",
                    "thinking_time": thinking_time,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": total_tokens,
                    "sources": sources,
                    "timing": {
                        "rewrite_ms": round((t_after_rewrite - t_start) * 1000, 1),
                        "retrieve_ms": round((t_after_retrieve - t_after_rewrite) * 1000, 1),
                        "rerank_ms": round((t_after_rerank - t_after_retrieve) * 1000, 1),
                        "generate_ms": round((t_end - t_after_rerank) * 1000, 1),
                    }
                }) + "\n"

                logger.info(f"RAG chat completed in {thinking_time}s with {chunk_count} chunks")

        except Exception as e:
            logger.error(f"Error in RAG chat stream: {e}")
            yield json.dumps({
                "type": "error",
                "message": str(e)
            }) + "\n"

    async def chat_stream(self, messages, use_rag=True, kb_name=None, session_id=""):
        if use_rag:
            async for chunk in self.rag_chat_stream(messages, kb_name=kb_name, session_id=session_id):
                yield chunk
        else:
            from services.chat_service import ChatService
            chat_service = ChatService()
            async for chunk in chat_service.chat_stream(messages):
                yield chunk
