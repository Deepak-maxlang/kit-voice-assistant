"""Deterministic business-rule checks run BEFORE any write reaches the college system.

Sources: KIT Student Handbook 2026 (Chapter 4 & 6, Chapter 10) and Circular 12/2026.
All "today" logic uses the college date from /v1/today, never the server clock.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable

MIN_ATTENDANCE = 75.0
REQUEST_TYPES = {"personal_leave", "medical_leave", "on_duty"}
ACTIVE_STATUSES = {"pending", "approved", "proof_pending", "proof pending"}


@dataclass
class RuleResult:
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: dict[str, Any] = field(default_factory=dict)

    def fail(self, msg: str) -> None:
        self.ok = False
        self.errors.append(msg)

    def to_dict(self) -> dict[str, Any]:
        return {"allowed": self.ok, "errors": self.errors, "warnings": self.warnings, **self.info}


def parse_date(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def daterange(start: date, end: date) -> Iterable[date]:
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def is_working_day(d: date, calendar_events: list[dict]) -> bool:
    if any(e.get("type") == "holiday" and e.get("date") == d.isoformat() and e.get("status", "active") == "active"
           for e in calendar_events):
        return False
    if any(e.get("type") in ("working_day", "special_working_day") and e.get("date") == d.isoformat()
           for e in calendar_events):
        return True
    return d.weekday() != 6  # Sunday off by default


def check_leave_request(req: dict, today: date, calendar_events: list[dict],
                        existing_requests: list[dict] | None = None,
                        attendance: dict | None = None,
                        timetable: list[dict] | None = None) -> RuleResult:
    """Validate a leave / on-duty request against handbook rules."""
    r = RuleResult()
    rtype = (req.get("type") or "").strip()
    if rtype not in REQUEST_TYPES:
        r.fail(f"Request type must be one of personal_leave, medical_leave or on_duty (got '{rtype}').")
        return r
    try:
        start, end = parse_date(req["from_date"]), parse_date(req.get("to_date") or req["from_date"])
    except (KeyError, ValueError):
        r.fail("Valid from_date and to_date (YYYY-MM-DD) are required.")
        return r
    if end < start:
        r.fail("The end date is before the start date (Rule 6.1.6).")
        return r
    if not (req.get("reason") or "").strip():
        r.fail("A reason is required for every request (Rule 6.1.6).")
    if rtype == "on_duty" and not (req.get("event_name") or "").strip():
        r.fail("On-duty requests must state the event name (Rule 6.4.2).")
    if req.get("half_day") and start != end:
        r.fail("Half-day leave covers a single day only (Rule 6.2.1).")

    # Timing rules
    if rtype == "personal_leave" and start <= today:
        r.fail(f"Personal leave must be applied at least 24 hours before it starts; retrospective or same-day personal "
               f"leave is not allowed (Rules 6.1.1, 6.1.2). Today is {today.isoformat()}.")
    if rtype == "on_duty" and start < today:
        r.fail("On-duty must be requested before the event (Rule 6.4.2).")
    if rtype == "medical_leave" and start > today + timedelta(days=30):
        r.warnings.append("Medical leave is normally applied for an illness that has started or is imminent.")

    # Calendar rules
    exams = [e for e in calendar_events if e.get("type") == "internal_exam" and e.get("status", "active") == "active"]
    for d in daterange(start, end):
        for e in exams:
            if e.get("date") == d.isoformat():
                if rtype == "on_duty":
                    r.fail(f"On-duty is not granted on {d.isoformat()} because of '{e.get('title')}' "
                           f"(Rule 6.4.7, unless approved in writing by the Principal for a national-level event).")
                else:
                    r.fail(f"Leave is not granted on {d.isoformat()} because of '{e.get('title')}' "
                           f"(Rule 6.1.4: no leave on internal assessment days).")
    working = [d for d in daterange(start, end) if is_working_day(d, calendar_events)]
    if not working:
        r.fail("All the requested dates are holidays, so no leave is needed.")
    elif len(working) < (end - start).days + 1:
        r.warnings.append("Some requested dates are holidays and are not counted.")
    if rtype == "personal_leave" and len(working) > 3:
        r.warnings.append("Personal leave over three consecutive working days also needs HOD approval (Rule 6.1.3).")
    if rtype == "on_duty":
        r.warnings.append("Submit proof of participation within 3 working days after the event (Circular 12/2026), "
                          "or the on-duty is rejected and counted as absent.")
    if rtype == "medical_leave":
        r.warnings.append("Submit the medical certificate within 3 working days of returning (Rule 6.3.3).")

    # Duplicates (Rule 6.6.3)
    for ex in existing_requests or []:
        if str(ex.get("status", "")).lower() not in ACTIVE_STATUSES:
            continue
        try:
            es, ee = parse_date(ex["from_date"]), parse_date(ex["to_date"])
        except (KeyError, ValueError):
            continue
        if es <= end and start <= ee:
            r.fail(f"You already have a {ex.get('status')} {ex.get('type')} request (#{ex.get('id')}) covering "
                   f"{ex['from_date']} to {ex['to_date']}; duplicate requests are not allowed (Rule 6.6.3).")

    # Attendance impact (Rule 4.2.2 / 6.1.5): leave periods count as absent.
    if rtype != "on_duty" and attendance and working:
        proj = project_attendance(attendance, timetable or [], working)
        r.info["attendance_projection"] = proj
        if proj and proj["projected_percentage"] < MIN_ATTENDANCE:
            r.warnings.append(
                f"This leave would bring your attendance from {proj['current_percentage']}% to about "
                f"{proj['projected_percentage']}%, below the 75% minimum (Chapter 4).")
    r.info["working_days"] = [d.isoformat() for d in working]
    return r


def project_attendance(attendance: dict, timetable: list[dict], leave_days: list[date]) -> dict | None:
    """Estimate attendance after missing the classes on ``leave_days`` (tracked subjects only)."""
    held = attendance.get("total_classes_held") or 0
    attended = attendance.get("total_classes_attended") or 0
    if not held:
        return None
    tracked = {s.get("subject_code") for s in attendance.get("subjects", [])}
    missed = 0
    for d in leave_days:
        weekday = d.isoweekday()  # API uses 1=Monday
        slots = [t for t in timetable if t.get("weekday") == weekday and (not tracked or t.get("subject_code") in tracked)]
        missed += len(slots) if timetable else 1
    new_held = held + missed
    return {
        "current_percentage": round(attended / held * 100, 1),
        "classes_missed": missed,
        "projected_percentage": round(attended / new_held * 100, 1) if new_held else 0.0,
    }


def check_placement_eligibility(drive: dict, profile: dict, academics: dict, today: date | None = None) -> RuleResult:
    """Handbook Chapter 10 style eligibility using the drive's published criteria."""
    r = RuleResult()
    year = profile.get("year_of_study")
    if drive.get("eligible_years") and year not in drive["eligible_years"]:
        r.fail(f"{drive.get('company')} is open only to year {', '.join(map(str, drive['eligible_years']))} students; "
               f"you are in year {year}.")
    dept = profile.get("department")
    if drive.get("allowed_departments") and dept not in drive["allowed_departments"]:
        r.fail(f"{drive.get('company')} is open only to {', '.join(drive['allowed_departments'])}; you are in {dept}.")
    cgpa = academics.get("cgpa")
    if cgpa is not None and drive.get("min_cgpa") is not None and cgpa < drive["min_cgpa"]:
        r.fail(f"Minimum CGPA is {drive['min_cgpa']}; yours is {cgpa}.")
    if not drive.get("allow_standing_arrears", True) and (academics.get("standing_arrears") or 0) > 0:
        r.fail("This drive does not allow standing arrears.")
    if academics.get("placed_company"):
        r.warnings.append(f"You are already placed at {academics['placed_company']}; placement policy may restrict further drives.")
    if today and drive.get("deadline") and parse_date(drive["deadline"]) < today:
        r.fail(f"The registration deadline ({drive['deadline']}) has passed.")
    if drive.get("seats_left") == 0:
        r.fail("No seats are left in this drive.")
    return r


def check_admission_eligibility(course: dict, twelfth_percent: float) -> RuleResult:
    r = RuleResult()
    minimum = course.get("min_12th_percent")
    if twelfth_percent is None or not (0 <= float(twelfth_percent) <= 100):
        r.fail("12th percentage must be between 0 and 100.")
    elif minimum is not None and float(twelfth_percent) < float(minimum):
        r.fail(f"{course.get('name')} needs at least {minimum}% in 12th; you have {twelfth_percent}%.")
    return r
