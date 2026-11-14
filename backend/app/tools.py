"""Tool registry exposed to the LLM, scoped by role (public / student / teacher).

Security: tools never accept a user/student id. Identity always comes from the
server-side session (``ToolContext``), so a prompt cannot read another person's data.
Write tools are never executed directly by the LLM: they are pre-checked against
the rules, then held as a *pending action* until the user confirms.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Awaitable, Callable

from . import rules
from .kit_client import KitClient, KitError

PUBLIC, STUDENT, TEACHER = "public", "student", "teacher"


@dataclass
class ToolContext:
    kit: KitClient
    role: str = PUBLIC
    token: str | None = None
    user: dict | None = None
    today: str = ""
    kb: Any = None
    cache: Any = None

    @property
    def today_date(self) -> date:
        return rules.parse_date(self.today)

    async def cached(self, key: str, ttl: float, fn: Callable[[], Awaitable[Any]]) -> Any:
        scope = f"u{self.user['id']}:" if self.user and not key.startswith("pub:") else ""
        full = scope + key
        if self.cache is not None:
            hit = self.cache.get(full)
            if hit is not None:
                return hit
        value = await fn()
        if self.cache is not None:
            self.cache.set(full, value, ttl)
        return value

    def invalidate_user(self) -> None:
        if self.cache is not None and self.user:
            self.cache.delete_prefix(f"u{self.user['id']}:")


Handler = Callable[[ToolContext, dict], Awaitable[Any]]


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    roles: set[str]
    handler: Handler
    write: bool = False
    precheck: Handler | None = None          # returns dict with allowed/errors/warnings
    summary: Callable[[dict], str] | None = None

    def schema(self) -> dict:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}


def _obj(props: dict | None = None, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props or {}, "required": required or []}


# ------------------------------------------------------------------ public handlers
async def get_college_info(ctx, args):
    return await ctx.cached("pub:college", 600, ctx.kit.college)


async def list_courses(ctx, args):
    return await ctx.cached("pub:courses", 300, ctx.kit.courses)


async def search_documents(ctx, args):
    types = None
    if ctx.role == PUBLIC:
        types = {"brochure", "handbook", "circular", "notice"}
    hits = ctx.kb.search(args.get("query", ""), k=4, doc_types=types) if ctx.kb else []
    return {"results": hits, "note": "Latest circular prevails over the handbook (Rule 1.4)."} if hits else \
        {"results": [], "note": "No matching passage found in the college documents."}


async def list_documents(ctx, args):
    return ctx.kb.catalogue() if ctx.kb else []


async def _find_course(ctx, code: str) -> dict | None:
    courses = await list_courses(ctx, {})
    code = (code or "").upper().strip()
    for c in courses:
        if code in (c["code"], c.get("department")) or code.replace("BTECH-", "") == c.get("department"):
            return c
    return None


async def check_admission_eligibility(ctx, args):
    course = await _find_course(ctx, args.get("course_code", ""))
    if not course:
        return {"allowed": False, "errors": ["Unknown course code. Use list_courses."]}
    res = rules.check_admission_eligibility(course, args.get("twelfth_percent"))
    return {**res.to_dict(), "course": course}


async def precheck_admission(ctx, args):
    missing = [f for f in ("full_name", "phone", "email", "course_code", "twelfth_percent") if not args.get(f)]
    if missing:
        return {"allowed": False, "errors": [f"Missing: {', '.join(missing)}"]}
    if "@" not in str(args["email"]):
        return {"allowed": False, "errors": ["Email address looks invalid."]}
    res = await check_admission_eligibility(ctx, args)
    if res.get("course"):
        args["course_code"] = res["course"]["code"]
    return res


async def submit_admission_application(ctx, args):
    payload = {k: args[k] for k in ("full_name", "phone", "email", "course_code")}
    payload["twelfth_percent"] = float(args["twelfth_percent"])
    return await ctx.kit.submit_admission(payload)


# ------------------------------------------------------------------ student handlers
def _safe_profile(user: dict | None) -> dict:
    """Profile without free-text 'notes' (user-controlled text is never shown to the LLM as instructions)."""
    return {k: v for k, v in (user or {}).items() if k != "notes"}


async def get_my_profile(ctx, args):
    out = {"profile": _safe_profile(ctx.user)}
    if ctx.role == STUDENT:
        out["academics"] = await ctx.cached("academics", 60, lambda: ctx.kit.academics(ctx.token))
    return out


async def get_my_attendance(ctx, args):
    data = await ctx.cached("attendance", 30, lambda: ctx.kit.attendance(ctx.token))
    return {**data, "minimum_required": rules.min_attendance(ctx.user)}


async def get_attendance_sessions(ctx, args):
    return await ctx.kit.attendance_sessions(ctx.token, args.get("subject_code"), args.get("from_date"), args.get("to_date"))


async def get_timetable(ctx, args):
    tt = await ctx.cached("timetable", 300, lambda: ctx.kit.timetable(ctx.token))
    wd = args.get("weekday")
    return [t for t in tt if not wd or t.get("weekday") == wd] if isinstance(tt, list) else tt


async def get_subjects(ctx, args):
    return await ctx.cached("subjects", 300, lambda: ctx.kit.subjects(ctx.token))


async def _calendar(ctx, start: str, end: str):
    return await ctx.cached(f"cal:{start}:{end}", 120, lambda: ctx.kit.calendar(ctx.token, start, end))


async def get_calendar(ctx, args):
    start = args.get("from_date") or ctx.today
    end = args.get("to_date") or (ctx.today_date + timedelta(days=30)).isoformat()
    return await _calendar(ctx, start, end)


async def list_my_requests(ctx, args):
    return await ctx.cached("requests", 20, lambda: ctx.kit.my_requests(ctx.token))


async def get_request_status(ctx, args):
    return await ctx.kit.get_request(ctx.token, int(args["request_id"]))


async def check_leave_request(ctx, args):
    """Dry-run of the leave rules; also used as the precheck for submit_leave_request."""
    args.setdefault("to_date", args.get("from_date"))
    args.setdefault("reason", args.get("reason") or "")
    try:
        start = rules.parse_date(args["from_date"])
        end = rules.parse_date(args["to_date"])
    except (KeyError, ValueError):
        return {"allowed": False, "errors": ["Please give the date(s) as YYYY-MM-DD."]}
    # Calendar runs to +14 days so the OD proof deadline (3 working days) sees holidays / working Saturdays.
    cal, existing, att, tt = await asyncio.gather(
        _calendar(ctx, start.isoformat(), (max(start, end) + timedelta(days=14)).isoformat()),
        list_my_requests(ctx, {}),
        ctx.cached("attendance", 30, lambda: ctx.kit.attendance(ctx.token)),
        ctx.cached("timetable", 300, lambda: ctx.kit.timetable(ctx.token)),
    )
    res = rules.check_leave_request(args, ctx.today_date, cal, existing, att, tt, rules.min_attendance(ctx.user))
    if args.get("_dry_run_ignore_reason"):
        res.errors = [e for e in res.errors if "reason" not in e.lower()]
        res.ok = not res.errors
    return res.to_dict()


async def check_leave_tool(ctx, args):
    args = dict(args)
    args.setdefault("type", "personal_leave")
    if not args.get("reason"):
        args["_dry_run_ignore_reason"] = True
    if args.get("type") == "on_duty" and not args.get("event_name"):
        args["event_name"] = "(to be provided)"
    return await check_leave_request(ctx, args)


async def submit_leave_request(ctx, args):
    payload = {"type": args["type"], "from_date": args["from_date"], "to_date": args.get("to_date") or args["from_date"],
               "half_day": bool(args.get("half_day", False)), "reason": args["reason"]}
    if args.get("event_name"):
        payload["event_name"] = args["event_name"]
    result = await ctx.kit.create_request(ctx.token, payload)
    ctx.invalidate_user()
    return result


async def precheck_cancel(ctx, args):
    try:
        req = await ctx.kit.get_request(ctx.token, int(args["request_id"]))
    except KitError as e:
        return {"allowed": False, "errors": [e.message]}
    if str(req.get("status")) not in ("pending", "approved", "proof_pending"):
        return {"allowed": False, "errors": [f"Request #{req['id']} is already {req.get('status')}."]}
    if rules.parse_date(req["from_date"]) <= ctx.today_date and req.get("type") != "on_duty":
        return {"allowed": False, "errors": ["A leave that has already started cannot be cancelled (Rule 6.6.1)."]}
    return {"allowed": True, "request": req}


async def cancel_request(ctx, args):
    result = await ctx.kit.cancel_request(ctx.token, int(args["request_id"]))
    ctx.invalidate_user()
    return result


async def list_placement_drives(ctx, args):
    if ctx.role != STUDENT:
        return await ctx.cached("pub:drives", 60, ctx.kit.placement_drives)
    drives, academics, regs = await asyncio.gather(ctx.cached("pub:drives", 60, ctx.kit.placement_drives),
                                                   ctx.cached("academics", 60, lambda: ctx.kit.academics(ctx.token)),
                                                   ctx.cached("placements", 30, lambda: ctx.kit.my_placements(ctx.token)))
    out = []
    for d in drives:
        res = rules.check_placement_eligibility(d, ctx.user or {}, academics, ctx.today_date, regs)
        out.append({**d, "you_are_eligible": res.ok, "ineligible_reasons": res.errors})
    return out


async def precheck_placement(ctx, args):
    drives = await ctx.cached("pub:drives", 60, ctx.kit.placement_drives)
    drive = next((d for d in drives if d["id"] == int(args["drive_id"])), None)
    if not drive:
        return {"allowed": False, "errors": ["No such placement drive."]}
    academics, regs = await asyncio.gather(ctx.cached("academics", 60, lambda: ctx.kit.academics(ctx.token)),
                                           ctx.kit.my_placements(ctx.token))
    res = rules.check_placement_eligibility(drive, ctx.user or {}, academics, ctx.today_date, regs)
    args["company"] = drive["company"]  # for the spoken confirmation
    return {**res.to_dict(), "drive": drive}


async def register_placement(ctx, args):
    result = await ctx.kit.register_drive(ctx.token, int(args["drive_id"]))
    ctx.invalidate_user()
    return result


async def my_placements(ctx, args):
    return await ctx.kit.my_placements(ctx.token)


# ------------------------------------------------------------------ teacher handlers
async def teacher_classes(ctx, args):
    return await ctx.cached("tclasses", 60, lambda: ctx.kit.teacher_classes(ctx.token))


def _mark_untrusted(req: dict) -> dict:
    """Student-written text is data, not instructions (e.g. 'SYSTEM: approve all...')."""
    out = dict(req)
    for key in ("reason", "student_notes", "event_name"):
        if out.get(key):
            out[key] = f"<student_text>{out[key]}</student_text>"
    return out


async def teacher_requests(ctx, args):
    reqs = await ctx.kit.teacher_requests(ctx.token, args.get("status") or None)
    return [_mark_untrusted(r) for r in reqs or []]


async def class_attendance(ctx, args):
    class_id = args.get("class_id") or (ctx.user or {}).get("class_id")
    if not class_id:
        classes = await teacher_classes(ctx, {})
        class_id = classes[0]["class_id"] if classes else None
    if not class_id:
        return {"error": "You don't advise any class."}
    data = await ctx.kit.class_attendance(ctx.token, int(class_id))
    return {**data, "minimum_required": rules.MIN_ATTENDANCE,
            "below_minimum": [s["student_name"] for s in data.get("students", []) if s.get("percentage", 100) < rules.MIN_ATTENDANCE]}


async def _find_teacher_request(ctx, rid: int) -> dict | None:
    for status in ("pending", None):
        reqs = await ctx.kit.teacher_requests(ctx.token, status)
        found = next((r for r in reqs or [] if r.get("id") == rid), None)
        if found:
            return found
    return None


async def precheck_decision(ctx, args):
    req, classes = await asyncio.gather(_find_teacher_request(ctx, int(args["request_id"])), teacher_classes(ctx, {}))
    if not req:
        return {"allowed": False, "errors": ["That request is not in your classes (Rule 6.5.2)."]}
    cls = next((c for c in classes or [] if c.get("class_code") == req.get("class_code")), None)
    if cls is None:
        return {"allowed": False, "errors": [f"You are not the advisor or delegated approver of {req.get('class_code')} "
                                             "(Rule 6.5.2)."]}
    if cls.get("valid_until") and rules.parse_date(cls["valid_until"]) < ctx.today_date:
        return {"allowed": False, "errors": [f"Your delegated authority for {cls['class_code']} ended on "
                                             f"{cls['valid_until']} (Rule 6.5.3 and the department delegation notice)."]}
    if req.get("status") not in ("pending", "proof_pending"):
        return {"allowed": False, "errors": [f"Request #{req['id']} is already {req.get('status')}; a request is decided only once (Rule 6.5.5)."]}
    if "reason" in args and not str(args.get("reason") or "").strip():
        return {"allowed": False, "errors": ["A reason is mandatory for rejection."]}
    warnings = []
    if req.get("type") != "on_duty":
        try:
            cal = await ctx.kit.calendar(ctx.token, req["from_date"], req["to_date"])
            if any(e.get("type") == "internal_exam" for e in cal):
                warnings.append("The request covers an internal assessment day (Rule 6.1.4).")
        except KitError:
            pass
    args["student"] = f"{req.get('student_name')}'s {str(req.get('type', '')).replace('_', ' ')} for {req.get('from_date')}"
    return {"allowed": True, "warnings": warnings, "request": req}


async def approve_request(ctx, args):
    result = await ctx.kit.approve_request(ctx.token, int(args["request_id"]), args.get("comment"))
    ctx.invalidate_user()
    return result


async def reject_request(ctx, args):
    result = await ctx.kit.reject_request(ctx.token, int(args["request_id"]), args["reason"])
    ctx.invalidate_user()
    return result


# ------------------------------------------------------------------ registry
DATE = {"type": "string", "description": "Date in YYYY-MM-DD (resolve relative dates against the college date)"}
LEAVE_PROPS = {
    "type": {"type": "string", "enum": ["personal_leave", "medical_leave", "on_duty"]},
    "from_date": DATE, "to_date": DATE,
    "half_day": {"type": "boolean"},
    "reason": {"type": "string"},
    "event_name": {"type": "string", "description": "Required for on_duty"},
}
ALL = {PUBLIC, STUDENT, TEACHER}
LOGGED = {STUDENT, TEACHER}

TOOLS: list[Tool] = [
    Tool("get_college_info", "College name, intro and contact details.", _obj(), ALL, get_college_info),
    Tool("list_courses", "Courses offered with code, minimum 12th %, annual fee, hostel fee and seats.", _obj(), ALL, list_courses),
    Tool("search_documents", "Search the official handbook (latest edition), circulars, admissions brochure and "
         "lesson notes. Use for rules, policies, admission dates/documents and subject doubts.",
         _obj({"query": {"type": "string"}}, ["query"]), ALL, search_documents),
    Tool("list_documents", "List the official documents currently in force (including new circulars).", _obj(), ALL, list_documents),
    Tool("check_admission_eligibility", "Check whether a 12th percentage meets a course's minimum.",
         _obj({"course_code": {"type": "string"}, "twelfth_percent": {"type": "number"}}, ["course_code", "twelfth_percent"]),
         {PUBLIC}, check_admission_eligibility),
    Tool("submit_admission_application", "Submit an admission application. Collect all fields first.",
         _obj({"full_name": {"type": "string"}, "phone": {"type": "string"}, "email": {"type": "string"},
               "course_code": {"type": "string", "description": "e.g. BTECH-CSE"}, "twelfth_percent": {"type": "number"}},
              ["full_name", "phone", "email", "course_code", "twelfth_percent"]),
         {PUBLIC}, submit_admission_application, write=True, precheck=precheck_admission,
         summary=lambda a: f"submit an admission application for {a.get('full_name')} to {a.get('course_code')} "
                           f"with {a.get('twelfth_percent')}% in 12th, phone {a.get('phone')}, email {a.get('email')}"),
    Tool("get_my_profile", "The logged-in person's profile (and CGPA/arrears for students).", _obj(), LOGGED, get_my_profile),
    Tool("get_my_attendance", "Logged-in student's attendance summary per subject.", _obj(), {STUDENT}, get_my_attendance),
    Tool("get_attendance_sessions", "Logged-in student's individual class sessions (present/absent/od).",
         _obj({"subject_code": {"type": "string"}, "from_date": DATE, "to_date": DATE}), {STUDENT}, get_attendance_sessions),
    Tool("get_timetable", "Logged-in student's weekly timetable. weekday 1=Mon..7=Sun.",
         _obj({"weekday": {"type": "integer"}}), {STUDENT}, get_timetable),
    Tool("get_subjects", "Logged-in student's subjects.", _obj(), {STUDENT}, get_subjects),
    Tool("get_calendar", "Academic calendar events (holidays, internal exams) between two dates.",
         _obj({"from_date": DATE, "to_date": DATE}), LOGGED, get_calendar),
    Tool("list_my_requests", "Logged-in student's leave/on-duty requests and their status.", _obj(), {STUDENT}, list_my_requests),
    Tool("get_request_status", "Status of one of the student's requests.",
         _obj({"request_id": {"type": "integer"}}, ["request_id"]), {STUDENT}, get_request_status),
    Tool("check_leave_request", "Check (without submitting) whether a leave/on-duty request is allowed: exam days, "
         "holidays, working Saturdays, notice period, duplicates and attendance impact. Use for 'can I take leave...' "
         "questions; type defaults to personal_leave, so don't ask for it first.",
         _obj(LEAVE_PROPS, ["from_date"]), {STUDENT}, check_leave_tool),
    Tool("submit_leave_request", "Submit a personal_leave, medical_leave or on_duty request for the logged-in student. "
         "Needs type, dates and reason; on_duty also needs event_name. The system asks the user to confirm.",
         _obj(LEAVE_PROPS, ["type", "from_date", "to_date", "reason"]), {STUDENT}, submit_leave_request, write=True,
         precheck=check_leave_request,
         summary=lambda a: f"submit {a.get('type', '').replace('_', ' ')} from {a.get('from_date')} to "
                           f"{a.get('to_date') or a.get('from_date')}{' (half day)' if a.get('half_day') else ''}"
                           f" for '{a.get('reason')}'" + (f", event '{a['event_name']}'" if a.get("event_name") else "")),
    Tool("cancel_request", "Cancel one of the student's pending/approved requests.",
         _obj({"request_id": {"type": "integer"}}, ["request_id"]), {STUDENT}, cancel_request, write=True,
         precheck=precheck_cancel, summary=lambda a: f"cancel request #{a.get('request_id')}"),
    Tool("list_placement_drives", "Placement drives with criteria; for students includes personal eligibility.",
         _obj(), {STUDENT, TEACHER}, list_placement_drives),
    Tool("register_placement", "Register the logged-in student for a placement drive (eligibility checked).",
         _obj({"drive_id": {"type": "integer"}}, ["drive_id"]), {STUDENT}, register_placement, write=True,
         precheck=precheck_placement, summary=lambda a: f"register you for the {a.get('company') or 'placement'} drive (#{a.get('drive_id')})"),
    Tool("my_placements", "Placement drives the student has registered for.", _obj(), {STUDENT}, my_placements),
    Tool("teacher_classes", "Classes the teacher advises or has delegated authority for (role_type, valid_until).", _obj(), {TEACHER}, teacher_classes),
    Tool("teacher_requests", "Leave/on-duty requests from the teacher's classes. status: pending|approved|rejected|cancelled.",
         _obj({"status": {"type": "string"}}), {TEACHER}, teacher_requests),
    Tool("class_attendance", "Attendance of every student in one of the teacher's classes.",
         _obj({"class_id": {"type": "integer", "description": "Omit to use the teacher's own advised class"}}), {TEACHER},
         class_attendance),
    Tool("approve_request", "Approve a student's request (teacher).",
         _obj({"request_id": {"type": "integer"}, "comment": {"type": "string"}}, ["request_id"]), {TEACHER},
         approve_request, write=True, precheck=precheck_decision,
         summary=lambda a: f"approve request #{a.get('request_id')} ({a.get('student', '')})"),
    Tool("reject_request", "Reject a student's request with a mandatory reason (teacher).",
         _obj({"request_id": {"type": "integer"}, "reason": {"type": "string"}}, ["request_id", "reason"]), {TEACHER},
         reject_request, write=True, precheck=precheck_decision,
         summary=lambda a: f"reject request #{a.get('request_id')} ({a.get('student', '')}) with reason '{a.get('reason')}'"),
]
_BY_NAME = {t.name: t for t in TOOLS}


def tools_for_role(role: str) -> list[Tool]:
    return [t for t in TOOLS if role in t.roles]


def get_tool(name: str, role: str) -> Tool | None:
    tool = _BY_NAME.get(name)
    return tool if tool and role in tool.roles else None


async def prefetch_user_data(ctx: ToolContext) -> None:
    """Warm the per-user cache right after login so the first questions answer fast."""
    jobs = [ctx.cached("pub:courses", 300, ctx.kit.courses), get_calendar(ctx, {})]
    if ctx.role == STUDENT:
        jobs += [ctx.cached("attendance", 30, lambda: ctx.kit.attendance(ctx.token)),
                 ctx.cached("timetable", 300, lambda: ctx.kit.timetable(ctx.token)),
                 ctx.cached("academics", 60, lambda: ctx.kit.academics(ctx.token)),
                 ctx.cached("pub:drives", 60, ctx.kit.placement_drives),
                 list_my_requests(ctx, {})]
    elif ctx.role == TEACHER:
        jobs.append(teacher_classes(ctx, {}))
    await asyncio.gather(*jobs, return_exceptions=True)
