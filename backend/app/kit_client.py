"""Typed async client for the KIT college system API.

Every call carries the participant key; authenticated calls also carry the
user's Bearer token. Errors are mapped to ``KitError`` using the API's
``{"error": {"code", "message", "field"}}`` envelope.
"""
from __future__ import annotations

import uuid
from typing import Any

import httpx


class KitError(Exception):
    def __init__(self, status: int, code: str, message: str, field: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.field = field

    def to_dict(self) -> dict[str, Any]:
        return {"error": {"status": self.status, "code": self.code, "message": self.message, "field": self.field}}


class KitClient:
    def __init__(self, base_url: str, participant_key: str, timeout: float = 15.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers={"X-Participant-Key": participant_key},
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, *, token: str | None = None,
                       params: dict | None = None, json: Any = None,
                       idempotent: bool = False, raw: bool = False) -> Any:
        headers: dict[str, str] = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if idempotent:
            headers["Idempotency-Key"] = str(uuid.uuid4())
        try:
            resp = await self._client.request(method, path, headers=headers, params=params, json=json)
        except httpx.HTTPError as exc:  # network / timeout
            raise KitError(503, "college_system_unreachable", f"College system unreachable: {exc.__class__.__name__}")
        if resp.status_code >= 400:
            try:
                err = resp.json().get("error") or {}
                if not err and "detail" in resp.json():
                    err = {"code": "validation_error", "message": str(resp.json()["detail"])[:300]}
            except Exception:
                err = {}
            raise KitError(resp.status_code, err.get("code", "http_error"),
                           err.get("message", resp.text[:200] or "Request failed"), err.get("field"))
        if raw:
            return resp.content
        if not resp.content:
            return None
        return resp.json()

    # ---- public ----
    async def health(self): return await self._request("GET", "/v1/health")
    async def today(self): return await self._request("GET", "/v1/today")
    async def college(self): return await self._request("GET", "/v1/college")
    async def courses(self): return await self._request("GET", "/v1/courses")
    async def documents(self): return await self._request("GET", "/v1/documents")
    async def document_file(self, doc_id: int) -> bytes:
        return await self._request("GET", f"/v1/documents/{doc_id}/file", raw=True)
    async def placement_drives(self): return await self._request("GET", "/v1/placements/drives")

    async def submit_admission(self, payload: dict):
        return await self._request("POST", "/v1/admissions/applications", json=payload, idempotent=True)

    # ---- auth ----
    async def login(self, login_id: str, password: str):
        return await self._request("POST", "/v1/auth/login", json={"login_id": login_id, "password": password})

    # ---- student ----
    async def me(self, token): return await self._request("GET", "/v1/me", token=token)
    async def academics(self, token): return await self._request("GET", "/v1/me/academics", token=token)
    async def subjects(self, token): return await self._request("GET", "/v1/me/subjects", token=token)
    async def timetable(self, token): return await self._request("GET", "/v1/me/timetable", token=token)
    async def calendar(self, token, date_from: str, date_to: str):
        return await self._request("GET", "/v1/calendar", token=token, params={"from": date_from, "to": date_to})
    async def attendance(self, token): return await self._request("GET", "/v1/me/attendance", token=token)
    async def attendance_sessions(self, token, subject: str | None = None, date_from: str | None = None, date_to: str | None = None):
        params = {k: v for k, v in {"subject": subject, "from": date_from, "to": date_to}.items() if v}
        return await self._request("GET", "/v1/me/attendance/sessions", token=token, params=params)
    async def my_requests(self, token): return await self._request("GET", "/v1/me/requests", token=token)
    async def get_request(self, token, request_id: int):
        return await self._request("GET", f"/v1/me/requests/{request_id}", token=token)
    async def create_request(self, token, payload: dict):
        return await self._request("POST", "/v1/me/requests", token=token, json=payload, idempotent=True)
    async def cancel_request(self, token, request_id: int):
        return await self._request("POST", f"/v1/me/requests/{request_id}/cancel", token=token)
    async def register_drive(self, token, drive_id: int):
        return await self._request("POST", f"/v1/placements/drives/{drive_id}/register", token=token)
    async def my_placements(self, token): return await self._request("GET", "/v1/me/placements", token=token)

    # ---- teacher ----
    async def teacher_classes(self, token): return await self._request("GET", "/v1/teacher/classes", token=token)
    async def teacher_requests(self, token, status: str | None = None):
        return await self._request("GET", "/v1/teacher/requests", token=token, params={"status": status} if status else None)
    async def class_attendance(self, token, class_id: int):
        return await self._request("GET", f"/v1/teacher/classes/{class_id}/attendance", token=token)
    async def approve_request(self, token, request_id: int, comment: str | None = None):
        return await self._request("POST", f"/v1/teacher/requests/{request_id}/approve", token=token, json={"comment": comment})
    async def reject_request(self, token, request_id: int, reason: str):
        return await self._request("POST", f"/v1/teacher/requests/{request_id}/reject", token=token, json={"reason": reason})
