"""Regression tests for bugs found in the v5 end-to-end run against the live sandbox."""
from datetime import date

from app import rules
from app.agent import Agent, is_plain_yes
from app.llm import FallbackLLM, LLMResult
from app.tools import spoken_date, spoken_range, get_tool
from tests.test_agent import FakeKit, FakeProvider, ctx, store  # noqa: F401  (fixture re-export)


def test_plain_yes_rejects_changed_details():
    assert is_plain_yes("yes") and is_plain_yes("Yes, go ahead") and is_plain_yes("ok please do")
    assert not is_plain_yes("yes but make it the 25th")
    assert not is_plain_yes("yes, change the reason to medical")
    assert not is_plain_yes("yes I also want to apply leave for next Monday and Tuesday please")
    assert not is_plain_yes("no")


async def test_yes_with_changes_does_not_execute_old_pending(store):  # noqa: F811
    kit = FakeKit()
    args = {"type": "personal_leave", "from_date": "2026-11-25", "to_date": "2026-11-25", "reason": "Family function"}
    new_args = dict(args, from_date="2026-11-26", to_date="2026-11-26")
    p = FakeProvider("gemini", [
        LLMResult(None, [{"id": "1", "name": "submit_leave_request", "args": args}], provider="gemini:x"),
        LLMResult(None, [{"id": "2", "name": "submit_leave_request", "args": new_args}], provider="gemini:x"),
    ])
    agent = Agent(FallbackLLM([p]), store)
    cid = store.create_conversation("user:88", "student", "leave")
    first = await agent.handle(ctx(kit), cid, "apply leave on 25 Nov for a family function")
    store.add_message(cid, "user", "apply leave"); store.add_message(cid, "assistant", first["reply"])
    second = await agent.handle(ctx(kit), cid, "yes but make it 26 November instead")
    assert kit.created == []  # the old 25 Nov request was NOT submitted
    assert second["actions"][-1]["status"] == "awaiting_confirmation"
    assert store.get_pending_action(cid)["args"]["from_date"] == "2026-11-26"
    assert "26 November" in second["reply"]


def test_spoken_dates_in_confirmation():
    assert spoken_date("2026-11-24") == "24 November"
    assert spoken_range("2026-11-24", "2026-11-24") == "on 24 November"
    assert spoken_range("2026-11-24", "2026-11-26") == "from 24 November to 26 November"
    tool = get_tool("submit_leave_request", "student")
    s = tool.summary({"type": "personal_leave", "from_date": "2026-11-24", "to_date": "2026-11-24", "reason": "x"})
    assert "2026-11-24" not in s and "on 24 November" in s


def test_low_attendance_warning_when_leave_day_has_no_tracked_class():
    att = {"total_classes_held": 50, "total_classes_attended": 36, "subjects": [{"subject_code": "CS304"}]}
    tt = [{"weekday": 1, "subject_code": "CS304"}]  # Tuesday has no CS304 class
    req = {"type": "personal_leave", "from_date": "2026-11-24", "to_date": "2026-11-24", "reason": "function"}
    r = rules.check_leave_request(req, date(2026, 11, 21), [], [], att, tt)
    text = " ".join(r.warnings)
    assert "already 72%" in text and "to about" not in text


async def test_calendar_note_added_for_students_only():
    agent = Agent(FallbackLLM([FakeProvider("x")]), None)
    note = await agent._calendar_note(ctx(FakeKit()))
    assert "2026-11-23 internal_exam: DBMS exam" in note and "authoritative" in note
    assert await agent._calendar_note(ctx(FakeKit(), "public")) == ""


class _KB:
    def __init__(self):
        from app.knowledge import Chunk
        self.chunks = [Chunk(1, "Notice CSE/2026/09: Delegation of approvals for CSE-2A", "notice", "2026-11-14",
                             "Valid: 14 November 2026 to 20 November 2026 To: Students of Class CSE-2A", [])]


class _TeacherKit:
    async def teacher_classes(self, token):
        return [{"class_id": 36, "class_code": "CSE-2A", "role_type": "delegated", "valid_until": "2026-11-27"}]

    async def teacher_requests(self, token, status=None):
        return [{"id": 116, "class_code": "CSE-2A", "status": "pending", "type": "personal_leave",
                 "from_date": "2026-11-24", "to_date": "2026-11-24", "student_name": "Arjun K"}]


async def test_expired_delegation_notice_overrides_stale_api_date():
    from app.tools import ToolContext, precheck_decision, teacher_classes
    c = ToolContext(kit=_TeacherKit(), role="teacher", token="t", user={"id": 86, "role": "teacher"},
                    today="2026-11-21", kb=_KB())
    classes = await teacher_classes(c, {})
    assert classes[0]["valid_until"] == "2026-11-20" and classes[0]["authority_expired"]
    res = await precheck_decision(c, {"request_id": 116})
    assert res["allowed"] is False and "ended on 2026-11-20" in res["errors"][0]


def _req(sid, ip="1.2.3.4"):
    from types import SimpleNamespace
    return SimpleNamespace(state=SimpleNamespace(session={"sid": sid}), headers={"x-forwarded-for": f"{ip}, 10.0.0.1"},
                           client=SimpleNamespace(host="10.0.0.1"))


def test_rate_limit_not_bypassed_by_fresh_sessions():
    import pytest
    from fastapi import HTTPException
    from app.ratelimit import RateLimiter
    from app.services import check_rate
    lim = RateLimiter(8)
    with pytest.raises(HTTPException) as e:
        for i in range(20):  # a new cookie-less session on every request, same IP and login id
            check_rate(lim, _req(f"s{i}"), subject="arjun")
    assert e.value.status_code == 429 and i == 8
    check_rate(lim, _req("other", ip="5.6.7.8"), subject="arjun")  # other IPs unaffected
