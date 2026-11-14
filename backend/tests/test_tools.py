from app.tools import PUBLIC, STUDENT, TEACHER, get_tool, tools_for_role


def names(role):
    return {t.name for t in tools_for_role(role)}


def test_public_cannot_see_student_or_teacher_tools():
    public = names(PUBLIC)
    for forbidden in ("get_my_attendance", "submit_leave_request", "list_my_requests", "get_my_profile",
                      "approve_request", "teacher_requests", "register_placement"):
        assert forbidden not in public
    assert {"list_courses", "submit_admission_application", "search_documents"} <= public


def test_student_cannot_use_teacher_tools():
    assert get_tool("approve_request", STUDENT) is None
    assert get_tool("submit_leave_request", STUDENT) is not None


def test_teacher_cannot_submit_student_requests():
    assert get_tool("submit_leave_request", TEACHER) is None
    assert get_tool("approve_request", TEACHER) is not None


def test_no_tool_accepts_a_user_id():
    for role in (PUBLIC, STUDENT, TEACHER):
        for t in tools_for_role(role):
            props = t.parameters.get("properties", {})
            assert not {"user_id", "student_id", "login_id"} & set(props), t.name


def test_write_tools_have_prechecks():
    for role in (PUBLIC, STUDENT, TEACHER):
        for t in tools_for_role(role):
            if t.write:
                assert t.precheck is not None and t.summary is not None, t.name
