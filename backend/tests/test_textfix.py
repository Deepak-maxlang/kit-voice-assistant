import pytest

from app.textfix import BAD_REPLY, EMPTY_REPLY, MAX_REPLY_CHARS, correct_transcript, validate_reply


@pytest.mark.parametrize("raw,fixed", [
    ("I need to know about the pieces", "I need to know about the fees"),
    ("what is the course piece for c s e", "what is the course fees for CSE"),
    ("what are the pieces for b tech", "what are the fees for B.Tech"),
    ("tell me the hostel pieces", "tell me the hostel fees"),
    ("hostile fees", "hostel fees"),
    ("how much is the fease", "how much is the fees"),
    ("I want on duty for the symposium", "I want on-duty for the symposium"),
    ("what is my a tendance", "what is my attendance"),
    ("admition process", "admission process"),
])
def test_corrections(raw, fixed):
    assert correct_transcript(raw) == fixed


@pytest.mark.parametrize("text", [
    "give me a piece of advice about leave",
    "I need peace of mind before the exam",
    "I broke the piece of my ID card",
    "explain 2NF and 3NF",
    "what is my attendance",
])
def test_no_false_fixes(text):
    assert correct_transcript(text) == text


def test_validator():
    assert validate_reply("The fee is Rs. 125000 per year.") == ("The fee is ₹1,25,000 per year.", [])
    assert validate_reply("**Hostel:** INR 60000")[0] == "Hostel: ₹60,000"
    assert validate_reply("") == (EMPTY_REPLY, ["empty"])
    assert validate_reply('{"tool": "list_courses"}')[0] == BAD_REPLY
    assert validate_reply("list_courses: ok")[0] == BAD_REPLY
    assert validate_reply("这是中文回答，不是英文。")[0] == BAD_REPLY
    long, problems = validate_reply("This is a sentence. " * 100)
    assert len(long) <= MAX_REPLY_CHARS and "truncated" in problems
    ok = "Your attendance is 76 percent, just above the 75 percent minimum."
    assert validate_reply(ok) == (ok, [])
