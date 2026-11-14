import pytest

from app.agent import Agent, is_no, is_yes
from app.llm import FallbackLLM, LLMResult, ProviderError
from app.memory import Store
from app.tools import ToolContext


class FakeProvider:
    def __init__(self, name, results=None, error=None):
        self.name, self.results, self.error, self.calls = name, list(results or []), error, 0

    async def generate(self, system, messages, tools):
        self.calls += 1
        if self.error:
            raise self.error
        return self.results.pop(0)


class FakeKit:
    def __init__(self):
        self.created = []

    async def courses(self):
        return [{"code": "BTECH-CSE", "name": "B.Tech CSE", "department": "CSE", "min_12th_percent": 75.0,
                 "annual_fee": 125000.0, "hostel_fee": 60000.0, "seats": 120}]

    async def calendar(self, token, a, b):
        return [{"date": "2026-11-23", "type": "internal_exam", "title": "DBMS exam", "status": "active"}]

    async def my_requests(self, token):
        return []

    async def attendance(self, token):
        return {"total_classes_held": 50, "total_classes_attended": 38, "subjects": []}

    async def timetable(self, token):
        return []

    async def create_request(self, token, payload):
        self.created.append(payload)
        return {"id": 999, "status": "pending", **payload}


@pytest.fixture
def store(tmp_path):
    return Store(str(tmp_path / "t.db"))


def ctx(kit, role="student"):
    user = {"id": 88, "full_name": "Arjun K", "role": role, "year_of_study": 2} if role != "public" else None
    return ToolContext(kit=kit, role=role, token="t" if user else None, user=user, today="2026-11-21")


async def test_fallback_to_second_provider():
    bad = FakeProvider("gemini", error=ProviderError("HTTP 429", retryable=True))
    good = FakeProvider("groq", [LLMResult("hello", provider="groq")])
    llm = FallbackLLM([bad, good], attempts=2, base_delay=0)
    res = await llm.generate("sys", [{"role": "user", "content": "hi"}], [])
    assert res.text == "hello" and bad.calls == 2 and good.calls == 1
    assert llm.status["gemini"]["ok"] is False and llm.status["groq"]["ok"] is True


async def test_non_retryable_error_skips_immediately():
    bad = FakeProvider("gemini", error=ProviderError("HTTP 400", retryable=False))
    good = FakeProvider("groq", [LLMResult("ok", provider="groq")])
    await FallbackLLM([bad, good], attempts=3, base_delay=0).generate("s", [], [])
    assert bad.calls == 1


async def test_all_providers_fail_gives_spoken_apology(store):
    llm = FallbackLLM([FakeProvider("a", error=ProviderError("down"))], attempts=1, base_delay=0)
    agent = Agent(llm, store)
    cid = store.create_conversation("user:88", "student", "x")
    out = await agent.handle(ctx(FakeKit()), cid, "hello")
    assert "trouble" in out["reply"]


async def test_read_tool_then_answer(store):
    p = FakeProvider("gemini", [
        LLMResult(None, [{"id": "1", "name": "list_courses", "args": {}}], provider="gemini:x"),
        LLMResult("The CSE fee is 125000 rupees.", provider="gemini:x"),
    ])
    agent = Agent(FallbackLLM([p]), store)
    cid = store.create_conversation("anon:1", "public", "fee")
    out = await agent.handle(ctx(FakeKit(), "public"), cid, "fee for CSE?")
    assert "125000" in out["reply"] and out["actions"][0]["tool"] == "list_courses"


async def test_public_user_cannot_call_student_tool(store):
    p = FakeProvider("gemini", [
        LLMResult(None, [{"id": "1", "name": "get_my_attendance", "args": {}}], provider="gemini:x"),
        LLMResult("Please log in.", provider="gemini:x"),
    ])
    agent = Agent(FallbackLLM([p]), store)
    cid = store.create_conversation("anon:1", "public", "x")
    out = await agent.handle(ctx(FakeKit(), "public"), cid, "my attendance")
    assert out["reply"] == "Please log in."


async def test_write_requires_confirmation_then_executes(store):
    kit = FakeKit()
    args = {"type": "personal_leave", "from_date": "2026-11-25", "to_date": "2026-11-25", "reason": "Family function"}
    p = FakeProvider("gemini", [
        LLMResult(None, [{"id": "1", "name": "submit_leave_request", "args": args}], provider="gemini:x"),
        LLMResult("Submitted, request 999.", provider="gemini:x"),
    ])
    agent = Agent(FallbackLLM([p]), store)
    cid = store.create_conversation("user:88", "student", "leave")
    first = await agent.handle(ctx(kit), cid, "apply leave on 25 Nov for a family function")
    assert "Shall I go ahead" in first["reply"] and kit.created == []
    store.add_message(cid, "user", "apply leave")
    store.add_message(cid, "assistant", first["reply"])
    second = await agent.handle(ctx(kit), cid, "yes please")
    assert kit.created and second["actions"][0]["status"] == "executed"
    assert store.get_pending_action(cid) is None


async def test_exam_day_write_is_blocked(store):
    kit = FakeKit()
    args = {"type": "personal_leave", "from_date": "2026-11-23", "to_date": "2026-11-23", "reason": "trip"}
    p = FakeProvider("gemini", [
        LLMResult(None, [{"id": "1", "name": "submit_leave_request", "args": args}], provider="gemini:x"),
        LLMResult("You can't take leave on the exam day.", provider="gemini:x"),
    ])
    agent = Agent(FallbackLLM([p]), store)
    cid = store.create_conversation("user:88", "student", "leave")
    out = await agent.handle(ctx(kit), cid, "leave on 23 Nov")
    assert out["actions"][0]["status"] == "blocked" and kit.created == []
    assert store.get_pending_action(cid) is None


def test_yes_no_detection():
    assert is_yes("Yes, go ahead") and is_yes("okay") and not is_yes("no thanks")
    assert is_no("No, cancel it") and not is_no("yes")
