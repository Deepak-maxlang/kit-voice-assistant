from datetime import date

from app import rules

TODAY = date(2026, 11, 21)
CAL = [
    {"date": "2026-11-22", "type": "holiday", "title": "Sunday Holiday", "status": "active"},
    {"date": "2026-11-23", "type": "internal_exam", "title": "DBMS Internal Assessment Exam", "status": "active"},
]
ATT = {"overall_percentage": 76.0, "total_classes_held": 50, "total_classes_attended": 38,
       "subjects": [{"subject_code": "CS304"}]}
TT = [{"weekday": 1, "subject_code": "CS304"}, {"weekday": 4, "subject_code": "CS304"}, {"weekday": 4, "subject_code": "CS304"}]


def leave(**kw):
    base = {"type": "personal_leave", "from_date": "2026-11-25", "to_date": "2026-11-25", "reason": "Family function"}
    base.update(kw)
    return base


def test_exam_day_blocked():
    r = rules.check_leave_request(leave(from_date="2026-11-23", to_date="2026-11-23"), TODAY, CAL)
    assert not r.ok and any("6.1.4" in e for e in r.errors)


def test_on_duty_on_exam_day_blocked():
    r = rules.check_leave_request(leave(type="on_duty", event_name="Hackathon", from_date="2026-11-23",
                                        to_date="2026-11-23"), TODAY, CAL)
    assert not r.ok and any("6.4.7" in e for e in r.errors)


def test_valid_leave_allowed():
    r = rules.check_leave_request(leave(), TODAY, CAL, [], ATT, TT)
    assert r.ok, r.errors


def test_on_duty_needs_event_name():
    r = rules.check_leave_request(leave(type="on_duty"), TODAY, CAL)
    assert not r.ok and any("event name" in e for e in r.errors)


def test_on_duty_warns_about_circular():
    r = rules.check_leave_request(leave(type="on_duty", event_name="Symposium"), TODAY, CAL)
    assert r.ok and any("Circular 12/2026" in w for w in r.warnings)


def test_personal_leave_needs_notice():
    r = rules.check_leave_request(leave(from_date="2026-11-21", to_date="2026-11-21"), TODAY, CAL)
    assert not r.ok


def test_medical_leave_can_be_retrospective():
    r = rules.check_leave_request(leave(type="medical_leave", from_date="2026-11-20", to_date="2026-11-20"), TODAY, CAL)
    assert r.ok, r.errors


def test_dates_in_wrong_order():
    r = rules.check_leave_request(leave(from_date="2026-11-26", to_date="2026-11-25"), TODAY, CAL)
    assert not r.ok


def test_holiday_only_request_rejected():
    r = rules.check_leave_request(leave(from_date="2026-11-22", to_date="2026-11-22"), TODAY, CAL)
    assert not r.ok


def test_duplicate_request_blocked():
    existing = [{"id": 37, "type": "on_duty", "from_date": "2026-11-25", "to_date": "2026-11-25", "status": "pending"}]
    r = rules.check_leave_request(leave(), TODAY, CAL, existing)
    assert not r.ok and any("6.6.3" in e for e in r.errors)


def test_attendance_warning_below_75():
    # Thursday has two CS304 periods: 38/52 = 73.1%
    r = rules.check_leave_request(leave(from_date="2026-11-26", to_date="2026-11-26"), TODAY, CAL, [], ATT, TT)
    assert r.ok
    assert any("below the 75%" in w for w in r.warnings)
    assert r.info["attendance_projection"]["projected_percentage"] < 75


DRIVE = {"id": 22, "company": "Zoho", "min_cgpa": 7.0, "allow_standing_arrears": False,
         "allowed_departments": ["CSE", "ECE"], "eligible_years": [4], "deadline": "2026-11-26", "seats_left": 1}


def test_placement_year_ineligible():
    r = rules.check_placement_eligibility(DRIVE, {"year_of_study": 2, "department": "CSE"},
                                          {"cgpa": 8.2, "standing_arrears": 0}, TODAY)
    assert not r.ok and "year 4" in r.errors[0]


def test_placement_eligible_final_year():
    r = rules.check_placement_eligibility(DRIVE, {"year_of_study": 4, "department": "CSE"},
                                          {"cgpa": 8.2, "standing_arrears": 0}, TODAY)
    assert r.ok


def test_placement_arrears_and_cgpa():
    r = rules.check_placement_eligibility(DRIVE, {"year_of_study": 4, "department": "MECH"},
                                          {"cgpa": 6.5, "standing_arrears": 2}, TODAY)
    assert len(r.errors) == 3


def test_admission_eligibility():
    course = {"name": "B.Tech CSE", "min_12th_percent": 75.0}
    assert rules.check_admission_eligibility(course, 80).ok
    assert not rules.check_admission_eligibility(course, 70).ok
