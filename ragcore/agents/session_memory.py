"""简单的会话记忆管理 — 基于内存存储，用于查询改写上下文补全"""
import time
from collections import defaultdict

MAX_PER_SESSION = 6
SESSION_TTL = 1800


class SessionMemory:
    def __init__(self):
        self._storage = defaultdict(list)
        self._last_access = {}

    def add(self, session_id, role, content):
        self._storage[session_id].append({"role": role, "content": content})
        if len(self._storage[session_id]) > MAX_PER_SESSION:
            self._storage[session_id] = self._storage[session_id][-MAX_PER_SESSION:]
        self._last_access[session_id] = time.time()

    def get_context(self, session_id):
        now = time.time()
        if session_id in self._last_access and now - self._last_access[session_id] > SESSION_TTL:
            self._storage.pop(session_id, None)
            return []
        self._last_access[session_id] = now
        return self._storage.get(session_id, [])


session_memory = SessionMemory()
