"""Deterministic text quality layers (no LLM calls).

* ``correct_transcript`` fixes common speech-recognition mishearings of college vocabulary before the
  transcript is shown, stored or sent to the LLM (e.g. "the pieces for CSE" -> "the fees for CSE").
  Ambiguous words are only fixed in a fee context, so "a piece of advice" stays untouched.
* ``validate_reply`` checks every reply before it is sent: non-empty, English, length-capped, no raw
  tool/JSON/debug text or markdown, consistent INR formatting. Failing replies get a safe message.
"""
from __future__ import annotations

import re

# ------------------------------------------------------------------ transcript correction
_FEE_WORDS = r"(pieces?|peaces?|fease|fis|fiz|fies|phees|fee's|fees')"
_FEE_CONTEXT = (r"(course|courses|hostel|annual|yearly|tuition|pay|paying|payment|structure|know about|how much|"
                r"total|cse|ece|mech|b\.?\s?tech|m\.?\s?tech|admission|college|semester|exam|kit)")
_FEE_BEFORE = re.compile(rf"\b{_FEE_CONTEXT}\s+(?:the\s+)?{_FEE_WORDS}\b", re.I)
_FEE_AFTER = re.compile(rf"\b{_FEE_WORDS}\s+(?=(for|structure|details|amount|per|in|at|to pay|is|are)\b)", re.I)
_ALWAYS_FEE = re.compile(r"\b(fease|phees|fies)\b", re.I)  # never real words here
_NOT_FEE = re.compile(r"\bpieces? of (advice|cake|paper|information|work|mind|code)\b|\bpeace\b(?! fees)", re.I)

_SIMPLE = [
    (r"\bon duty\b", "on-duty"),
    (r"\bo\.?\s?d\b(?= (request|leave|form|for)\b)", "OD"),
    (r"\bc\s?s\s?e\b", "CSE"),
    (r"\be\s?c\s?e\b", "ECE"),
    (r"\bhostile\b", "hostel"),
    (r"\bb\.?\s?tech\b", "B.Tech"),
    (r"\bm\.?\s?tech\b", "M.Tech"),
    (r"\ba tendance\b|\battendence\b|\battendants\b(?= (percentage|percent|is|of my)\b)", "attendance"),
    (r"\badmition\b|\badmisson\b", "admission"),
    (r"\bc\s?s\s?3\s?0\s?4\b", "CS304"),
    (r"\bd\s?b\s?m\s?s\b", "DBMS"),
    (r"\bknow topian\b|\bnotopian\b", "Knotopian"),
]
_SIMPLE_RE = [(re.compile(p, re.I), r) for p, r in _SIMPLE]


def correct_transcript(text: str) -> str:
    if not text:
        return text
    out = text
    for rx, rep in _SIMPLE_RE:
        out = rx.sub(rep, out)
    out = _ALWAYS_FEE.sub("fees", out)
    if not _NOT_FEE.search(out):
        out = _FEE_BEFORE.sub(lambda m: m.group(0)[: m.start(2) - m.start(0)] + "fees", out)
        out = _FEE_AFTER.sub("fees ", out)
    return out


# ------------------------------------------------------------------ reply validation
MAX_REPLY_CHARS = 900
EMPTY_REPLY = "Sorry, I didn't catch that. Could you ask again?"
BAD_REPLY = "Sorry, something went wrong with that answer. Could you ask it again, maybe in different words?"
_DEBUG = re.compile(r"(\{\s*\"[a-z_]+\"\s*:|\btool_calls?\b|\bfunction_call\b|\btraceback\b|\b[a-z]+_[a-z_]+\s*:\s*ok\b|"
                    r"\bNone\b\s*$|\bnull\b\s*$|^\s*\[|\bundefined\b)", re.I | re.M)
_MD = [(re.compile(r"\*\*(.+?)\*\*"), r"\1"), (re.compile(r"(?m)^\s*#{1,6}\s*"), ""),
       (re.compile(r"(?m)^\s*[-*•]\s+"), ""), (re.compile(r"`([^`]*)`"), r"\1")]
_INR = re.compile(r"(?:\bRs\.?\s?|\bINR\s?|₹\s?)(\d[\d,]*)(?:\.00)?\b", re.I)


def _indian_grouping(n: int) -> str:
    s = str(n)
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", head)
    return f"{head},{tail}"


def _fmt_inr(m: re.Match) -> str:
    digits = m.group(1).replace(",", "")
    return f"₹{_indian_grouping(int(digits))}" if digits.isdigit() else m.group(0)


def validate_reply(reply: str | None) -> tuple[str, list[str]]:
    """Return (safe_reply, problems). ``problems`` is empty when the reply passed unchanged in substance."""
    problems: list[str] = []
    text = (reply or "").strip()
    if not text:
        return EMPTY_REPLY, ["empty"]
    if _DEBUG.search(text):
        return BAD_REPLY, ["debug_text"]
    letters = [c for c in text if c.isalpha()]
    if letters and sum(c.isascii() for c in letters) / len(letters) < 0.8:
        return BAD_REPLY, ["not_english"]
    for rx, rep in _MD:
        text = rx.sub(rep, text)
    text = re.sub(r"\s*\n\s*", " ", text)
    text = _INR.sub(_fmt_inr, text)
    if len(text) > MAX_REPLY_CHARS:
        cut = text[:MAX_REPLY_CHARS]
        end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
        text = cut[: end + 1] if end > 200 else cut.rstrip() + "…"
        problems.append("truncated")
    return text, problems
