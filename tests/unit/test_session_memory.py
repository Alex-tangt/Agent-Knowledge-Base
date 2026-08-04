import time

import pytest

from agents.session_memory import SessionMemory, MAX_PER_SESSION, SESSION_TTL


class TestSessionMemoryAdd:
    @pytest.fixture
    def mem(self):
        return SessionMemory()

    def test_add_single_turn(self, mem):
        mem.add("s1", "user", "问题A")
        ctx = mem.get_context("s1")
        assert len(ctx) == 1
        assert ctx[0] == {"role": "user", "content": "问题A"}

    def test_add_assistant_response(self, mem):
        mem.add("s1", "user", "问题")
        mem.add("s1", "assistant", "回答")
        ctx = mem.get_context("s1")
        assert len(ctx) == 2
        assert ctx[1]["role"] == "assistant"
        assert ctx[1]["content"] == "回答"

    def test_multiple_sessions_isolated(self, mem):
        mem.add("s1", "user", "A")
        mem.add("s2", "user", "B")
        assert len(mem.get_context("s1")) == 1
        assert len(mem.get_context("s2")) == 1
        assert mem.get_context("s1")[0]["content"] == "A"
        assert mem.get_context("s2")[0]["content"] == "B"

    def test_overflow_trims_oldest(self, mem):
        for i in range(MAX_PER_SESSION + 2):
            mem.add("s1", "user", f"消息{i}")
        ctx = mem.get_context("s1")
        assert len(ctx) == MAX_PER_SESSION
        assert ctx[0]["content"] == "消息2"
        assert ctx[-1]["content"] == f"消息{MAX_PER_SESSION + 1}"

    def test_exactly_max_no_trim(self, mem):
        for i in range(MAX_PER_SESSION):
            mem.add("s1", "user", f"消息{i}")
        ctx = mem.get_context("s1")
        assert len(ctx) == MAX_PER_SESSION


class TestSessionMemoryGetContext:
    @pytest.fixture
    def mem(self):
        return SessionMemory()

    def test_unknown_session_returns_empty(self, mem):
        ctx = mem.get_context("nonexistent")
        assert ctx == []

    def test_ttl_expiry_clears_session(self, mem):
        mem.add("s1", "user", "问题")
        mem._last_access["s1"] = time.time() - SESSION_TTL - 10
        ctx = mem.get_context("s1")
        assert ctx == []

    def test_ttl_not_yet_expired(self, mem):
        mem.add("s1", "user", "问题")
        mem._last_access["s1"] = time.time() - (SESSION_TTL - 60)
        ctx = mem.get_context("s1")
        assert len(ctx) == 1

    def test_get_context_updates_last_access(self, mem):
        mem.add("s1", "user", "问题")
        old = mem._last_access["s1"]
        mem._last_access["s1"] = old - 5
        mem.get_context("s1")
        assert mem._last_access["s1"] >= old

    def test_ttl_expired_removes_from_storage(self, mem):
        mem.add("s1", "user", "问题")
        mem._last_access["s1"] = time.time() - SESSION_TTL - 10
        mem.get_context("s1")
        assert "s1" not in mem._storage
