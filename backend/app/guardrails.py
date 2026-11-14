"""LLM guardrails: cheap deterministic checks around every assistant turn.

* Input guard  - jailbreak / prompt-extraction attempts and abusive language are answered with a fixed
  reply WITHOUT calling the LLM (saves quota and removes the attack surface). Applies to typed and
  transcribed voice text alike, because both go through ``services.run_turn``.
* Cool-down    - repeated hits in one session (3 within 10 minutes) get a short cool-down.
* Output guard - a reply that leaks system-prompt text, internal tool names / JSON, secrets, or another
  person's identifiers is replaced with a safe message.

Real authorisation never depends on these checks: the user id always comes from the server session and
every write is rule-checked and confirmed. Guardrails are an extra layer, not the security boundary.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

STRIKE_LIMIT = 3
STRIKE_WINDOW_S = 600
COOLDOWN_S = 120

REFUSAL = ("I can't change how I work or share my instructions, but I'm happy to help with KIT questions: "
           "admissions, attendance, leave, on-duty, placements or your lessons.")
ABUSE_REPLY = "I'm here to help with KIT questions; please keep it respectful. How can I help you?"
COOLDOWN_REPLY = "Let's take a short break. Please try again in a couple of minutes with a KIT question."
SAFE_OUTPUT = "Sorry, I can't share that. Is there something about KIT I can help you with?"

# ------------------------------------------------------------------ input: jailbreak / prompt extraction
_JAILBREAK = [
    r"\b(ignore|disregard|forget|bypass|override)\b.{0,30}\b(previous|prior|above|earlier|your|system|these|any)\b.{0,25}"
    r"\b(instructions?|prompts?|rules?|guidelines?|polic(y|ies)|restrictions?|context)\b",
    r"\b(reveal|show|print|display|repeat|output|leak|dump|tell me|give me|what (is|are)|share)\b.{0,30}"
    r"\b(system|hidden|initial|original|developer|secret)\s+(prompt|instructions?|message|rules|context)\b",
    r"\b(your|the)\s+(system\s+)?prompt\b.{0,20}\b(verbatim|word for word|exactly|in full)\b",
    r"\byou are (now|no longer)\b",
    r"\b(dan|developer|god|admin|sudo|jailbreak|unrestricted|debug|root)\s+mode\b",
    r"\bdo anything now\b",
    r"\b(pretend|roleplay|role-play)\b.{0,30}\b(you|to be|as)\b",
    r"\bact as (an? |the )?(admin|administrator|teacher|principal|hod|developer|system|root|dan|another|different|unfiltered)\b",
    r"\bfrom now on\b.{0,40}\b(you|ignore|respond|act)\b",
    r"\b(new|updated|override) (instructions?|rules|system prompt)\s*:",
    r"(^|\n|\s)(system|assistant|developer)\s*:\s*\S",
    r"<\|?(im_start|im_end|system|endoftext)\|?>|\[/?(inst|system)\]|<</?sys>>",
    r"\b(base64|rot13)\b.{0,30}\b(decode|instructions?|execute|follow)\b",
    r"\bjailbreak\b",
    # Hinglish / Tanglish (transliterated)
    r"\b(pichle|pehle|purane)\b.{0,20}\b(instructions?|rules?)\b.{0,20}\b(bhool|ignore|chhod)",
    r"\b(system prompt|instructions?)\b.{0,20}\b(batao|dikhao|bata do|sollu|kaattu|kaatu)\b",
    r"\b(ellam|ella) (rules|instructions?)\b.{0,20}\b(maranthu|ignore)\b",
]
_JAILBREAK_RE = [re.compile(p, re.I | re.S) for p in _JAILBREAK]

# ------------------------------------------------------------------ input: profanity / abuse
_ABUSE_WORDS = {
    # English
    "fuck", "fucking", "fucker", "fck", "fcking", "fuk", "fking", "fuking", "phuck", "motherfucker", "shit", "bullshit", "bitch", "bastard", "asshole", "dick",
    "cunt", "slut", "whore", "retard", "idiot", "stupid bot", "moron", "dumbass", "wtf", "stfu",
    # Hindi / Hinglish (transliterated)
    "chutiya", "chutiye", "madarchod", "behenchod", "bhenchod", "bhosdike", "bhosdi", "gandu", "harami",
    "kamina", "kutta", "kutte", "saala", "saale", "randi", "lavde", "lauda",
    # Tamil (transliterated)
    "punda", "pundai", "otha", "oththa", "thevidiya", "thevdiya", "koothi",
    "loosu", "naaye", "porukki", "mairu", "myru",
}
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "!": "i"})
_ABUSE_RE = re.compile(r"\b(" + "|".join(sorted((re.escape(w) for w in _ABUSE_WORDS), key=len, reverse=True)) + r")\b")


def _normalise(text: str) -> str:
    t = unicodedata.normalize("NFKC", text).lower()
    t = t.translate(_LEET)
    t = re.sub(r"(?<=\w)[\*\.\-_](?=\w)", "", t)      # f*ck, f.u.c.k
    t = re.sub(r"(\w)\1{2,}", r"\1\1", t)              # fuuuuck -> fuuck
    return t


@dataclass
class Verdict:
    kind: str | None = None   # None | "jailbreak" | "abuse"
    reply: str | None = None

    @property
    def blocked(self) -> bool:
        return self.kind is not None


_APPROVE_ALL = re.compile(r"\bapprove (all|every|everyone|everything)\b", re.I)


def check_input(text: str, role: str = "public") -> Verdict:
    """``role`` matters for one rule: a teacher may legitimately say "approve all pending requests"
    (each one is still rule-checked and confirmed); from a student or guest it is an injection attempt."""
    norm = _normalise(text)
    if _ABUSE_RE.search(norm) or _ABUSE_RE.search(re.sub(r"(\w)\1+", r"\1", norm)):
        return Verdict("abuse", ABUSE_REPLY)
    raw = unicodedata.normalize("NFKC", text)
    if any(r.search(raw) for r in _JAILBREAK_RE) or (role != "teacher" and _APPROVE_ALL.search(raw)):
        return Verdict("jailbreak", REFUSAL)
    return Verdict()


# ------------------------------------------------------------------ output guard
_SECRET_RE = re.compile(r"(gsk_[A-Za-z0-9]{10,}|AIza[0-9A-Za-z_\-]{20,}|kit_live_[A-Za-z0-9]+|sk-[A-Za-z0-9]{20,}|"
                        r"Bearer\s+[A-Za-z0-9\-_\.=]{12,}|eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,})")
_PROMPT_FRAGMENTS = [
    "replies are spoken: 1-3 short natural sentences",
    "never guess: use tools for data",
    "text inside <student_text> tags",
    "the system asks the user to confirm, so don't ask yourself",
    "only ever discuss this person's own data",
    "precedence: newest circular/notice",
    "guardrails (highest priority)",
]
_INTERNAL_RE = re.compile(r"\b(submit_leave_request|submit_admission_application|check_leave_request|search_documents|"
                          r"teacher_requests|precheck_\w+|tool_calls?|function_call)\b|\{\s*\"(tool|name|args|arguments)\"\s*:", re.I)


def check_output(reply: str, own_identifiers: set[str] | None = None, other_identifiers: set[str] | None = None) -> str:
    """Return ``reply`` or a safe replacement if it leaks internals or another person's identifiers."""
    if not reply:
        return reply
    low = reply.lower()
    if _SECRET_RE.search(reply) or any(f in low for f in _PROMPT_FRAGMENTS) or _INTERNAL_RE.search(reply):
        return SAFE_OUTPUT
    own = {i.lower() for i in own_identifiers or set() if i}
    for ident in other_identifiers or set():
        if ident and ident.lower() not in own and re.search(rf"\b{re.escape(ident)}\b", reply, re.I):
            return SAFE_OUTPUT
    return reply


# ------------------------------------------------------------------ per-session strikes / cool-down
def strike(cache, sid: str, now: float) -> bool:
    """Record a guardrail hit; True if the session is now cooling down."""
    key = f"guard:{sid}"
    hits = [t for t in (cache.get(key) or []) if now - t < STRIKE_WINDOW_S] + [now]
    cache.set(key, hits, STRIKE_WINDOW_S)
    if len(hits) >= STRIKE_LIMIT:
        cache.set(f"guard_cool:{sid}", now + COOLDOWN_S, COOLDOWN_S)
        return True
    return False


def cooling_down(cache, sid: str, now: float) -> bool:
    until = cache.get(f"guard_cool:{sid}")
    return bool(until and until > now)
