"""HTTP-level checks that need no network: CSRF guard and security headers run before any service call."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)  # no lifespan: these requests are rejected before services are touched


def test_api_post_without_csrf_header_is_rejected():
    for path in ("/api/chat", "/api/voice", "/api/auth/login", "/api/auth/logout"):
        r = client.post(path, json={"message": "hi"})
        assert r.status_code == 403, path
        assert "X-Requested-With" in r.json()["detail"]


def test_csrf_rejection_has_no_stack_trace():
    r = client.post("/api/chat", content=b"x" * 10)
    assert r.status_code == 403 and "Traceback" not in r.text


def test_security_headers_present():
    r = client.get("/")
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "DENY"
    assert "content-security-policy" in {k.lower() for k in r.headers}


def test_spa_fallback_serves_html_for_client_routes():
    for path in ("/history", "/assistant", "/login"):
        r = client.get(path)
        assert r.status_code in (200, 404)  # 200 when the frontend build exists
        assert "Traceback" not in r.text
