from app.knowledge import KnowledgeBase, active_documents

DOCS = [
    ({"id": 1, "title": "Handbook 2025", "type": "handbook", "version": "2025.1", "effective_date": "2025-07-01"},
     "Personal leave must be applied 48 hours in advance."),
    ({"id": 2, "title": "Handbook 2026", "type": "handbook", "version": "2026.1", "effective_date": "2026-07-01"},
     "Personal leave must be applied 24 hours in advance. On-duty proof within 5 working days."),
    ({"id": 5, "title": "Circular 12/2026", "type": "circular", "version": "12/2026", "effective_date": "2026-11-01"},
     "On-duty proof must be submitted within 3 working days after the event."),
    ({"id": 4, "title": "DBMS notes", "type": "lesson_notes", "version": "1", "effective_date": "2026-10-22"},
     "Normalisation removes redundancy: 1NF, 2NF, 3NF and BCNF."),
]


def test_superseded_handbook_excluded():
    active = active_documents([m for m, _ in DOCS])
    assert {d["id"] for d in active} == {2, 4, 5}


def test_future_documents_excluded():
    assert all(d["id"] != 5 for d in active_documents([m for m, _ in DOCS], today="2026-10-30"))


def test_search_prefers_latest_rules():
    kb = KnowledgeBase()
    kb.load_static(DOCS)
    hits = kb.search("how many hours in advance for personal leave")
    assert hits[0]["source"] == "Handbook 2026"
    assert kb.search("on-duty proof deadline")[0]["source"] == "Circular 12/2026"


def test_us_spelling_matches_uk_notes():
    kb = KnowledgeBase()
    kb.load_static(DOCS)
    assert kb.search("what is normalization")[0]["source"] == "DBMS notes"


def test_public_filter():
    kb = KnowledgeBase()
    kb.load_static(DOCS)
    assert kb.search("normalisation", doc_types={"handbook", "circular", "brochure"}) == []
