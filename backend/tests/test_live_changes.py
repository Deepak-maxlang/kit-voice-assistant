"""Rules after the surprise documents: rescheduled CS304 exam, working Saturday, delegation notice."""
import asyncio
from datetime import date

import pytest

from app import rules
from app.knowledge import KnowledgeBase
from app.llm import FallbackLLM, LLMResult, ProviderError
from app.tools import ToolContext, precheck_decision

TODAY = date(2026, 11, 21)
# Live calendar after Circular 14/2026 and 15/2026
CAL = [
    {"date": "2026-11-22", "type": "holiday", "title": "Sunday Holiday", "status": "active"},
    {"date": "2026-11-23", "type": "internal_exam", "title": "DBMS Internal Assessment Exam", "status": "cancelled"},
    {"date": "2026-11-25", "type": "internal_exam", "title": "DBMS Internal Assessment Exam (Rescheduled)",
     "status": "active", "subject_code": "CS304"},
    {"date": "2026-11-28", "type": "working_day", "title": "Working Saturday (Follows Monday Timetable)",
     "status": "active", "follows_weekday": 1},
]
ATT = {"total_classes_held": 50, "total_classes_attended": 38, "subjects": [{"subject_code": "CS304"}]}
TT = [{"weekday": 1, "subject_code": "CS304"}, {"weekday": 4, "subject_code": "CS304"}]


def leave(d, **kw):
    return {"type": "personal_leave", "from_date": d, "to_date": d, "reason": "Family function", **kw}


def test_old_exam_date_no_longer_blocked():
    assert rules.check_leave_request(leave("2026-11-23"), TODAY, CAL).ok


def test_rescheduled_exam_date_blocked():
    r = rules.check_leave_request(leave("2026-11-25"), TODAY, CAL)
    assert not r.ok and "Rescheduled" in r.errors[0]


def test_declared_saturday_is_working_day_and_follows_monday():
    assert rules.is_working_day(date(2026, 11, 28), CAL)
    assert not rules.is_working_day(date(2026, 12, 5), CAL)  # ordinary Saturday
    r = rules.check_leave_request(leave("2026-11-28"), TODAY, CAL, [], ATT, TT)
    assert r.ok and r.info["working_days"] == ["2026-11-28"]
    assert r.info["attendance_projection"]["classes_missed"] == 1  # Monday timetable has CS304


def test_od_proof_counts_working_saturday():
    # Event Thu 26 Nov -> Fri 27 (1), Sat 28 working (2), Sun holiday, Mon 30 (3)
    r = rules.check_leave_request(leave("2026-11-26", type="on_duty", event_name="Hackathon"), TODAY, CAL)
    assert r.ok and r.info["od_proof_due"] == "2026-11-30"
    assert rules.add_working_days(date(2026, 12, 3), 3, []) == date(2026, 12, 8)  # Fri, (Sat/Sun off), Mon, Tue


def test_newest_notice_found_for_approver_question():
    kb = KnowledgeBase()
    kb.load_static([
        ({"id": 2, "title": "Handbook 2026", "type": "handbook", "version": "1", "effective_date": "2026-07-01"},
         "Requests are approved by the Class Advisor of the student's class (Rule 6.5.1)."),
        ({"id": 8, "title": "Notice CSE/2026/09", "type": "notice", "version": "1", "effective_date": "2026-11-21"},
         "Prof. Senthil will approve or reject the leave and on-duty requests of CSE-2A while the Class Advisor is on leave."),
    ])
    assert kb.search("who approves leave requests for CSE-2A")[0]["source"] == "Notice CSE/2026/09"
    assert kb.latest_updates()[0].startswith("Notice CSE/2026/09")


class TeacherKit:
    def __init__(self, classes):
        self.classes = classes

    async def teacher_requests(self, token, status=None):
        return [{"id": 5, "class_code": "CSE-2A", "status": "pending", "type": "on_duty",
                 "from_date": "2026-11-30", "to_date": "2026-11-30"}]

    async def teacher_classes(self, token):
        return self.classes


def tctx(kit):
    return ToolContext(kit=kit, role="teacher", token="t", user={"id": 7, "role": "teacher"}, today="2026-11-21")


async def test_expired_delegation_blocks_approval():
    kit = TeacherKit([{"class_id": 36, "class_code": "CSE-2A", "role_type": "delegated", "valid_until": "2026-11-20"}])
    res = await precheck_decision(tctx(kit), {"request_id": 5})
    assert not res["allowed"] and "ended" in res["errors"][0]


async def test_active_advisor_can_approve():
    kit = TeacherKit([{"class_id": 36, "class_code": "CSE-2A", "role_type": "advisor", "valid_until": None}])
    assert (await precheck_decision(tctx(kit), {"request_id": 5}))["allowed"]


class SlowProvider:
    name = "slow"

    async def generate(self, system, messages, tools):
        await asyncio.sleep(5)
        return LLMResult("too late", provider="slow")


class FastProvider:
    name = "fast"

    async def generate(self, system, messages, tools):
        return LLMResult("fast answer", provider="fast")


async def test_timeout_falls_back_to_next_provider():
    llm = FallbackLLM([SlowProvider(), FastProvider()], attempts=2, base_delay=0, call_timeout=0.05)
    res = await llm.generate("s", [], [])
    assert res.text == "fast answer"
    assert "timeout" in llm.status["slow"]["last_error"]


async def test_timeout_on_all_providers_raises():
    llm = FallbackLLM([SlowProvider()], attempts=1, base_delay=0, call_timeout=0.05)
    with pytest.raises(ProviderError):
        await llm.generate("s", [], [])
