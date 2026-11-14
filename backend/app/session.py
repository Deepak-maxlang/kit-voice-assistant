"""Signed, httpOnly session cookie that maps to a server-side session row."""
from fastapi import Request, Response
from itsdangerous import BadSignature, URLSafeSerializer

SESSION_COOKIE = "kit_sid"


def _serializer(settings) -> URLSafeSerializer:
    return URLSafeSerializer(settings.session_secret, salt="kit-session")


def set_session_cookie(response: Response, sid: str, settings) -> None:
    response.set_cookie(SESSION_COOKIE, _serializer(settings).dumps(sid), httponly=True, samesite="lax",
                        secure=settings.cookie_secure, max_age=8 * 3600, path="/")


def load_session(request: Request, store, settings) -> tuple[dict, bool]:
    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        try:
            sid = _serializer(settings).loads(raw)
            session = store.get_session(sid)
            if session:
                return session, False
        except BadSignature:
            pass
    sid = store.create_session()
    return {"sid": sid, "kit_token": None, "user": None}, True
