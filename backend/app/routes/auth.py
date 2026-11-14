from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..kit_client import KitError
import asyncio

from ..services import Services, check_rate, current_session, services, tool_context
from ..tools import prefetch_user_data
from ..session import SESSION_COOKIE, set_session_cookie

router = APIRouter(prefix="/api/auth", tags=["auth"])
_background: set[asyncio.Task] = set()  # keep references so prefetch tasks aren't garbage-collected


class LoginBody(BaseModel):
    login_id: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


def public_user(user: dict | None) -> dict | None:
    if not user:
        return None
    keys = ("id", "login_id", "full_name", "role", "department", "class_code", "programme", "year_of_study")
    return {k: user.get(k) for k in keys}


@router.post("/login")
async def login(body: LoginBody, request: Request, response: Response, svc: Services = Depends(services)):
    check_rate(svc.login_limiter, request, subject=body.login_id.strip())
    try:
        data = await svc.kit.login(body.login_id.strip(), body.password)
        token = data["access_token"]
        user = await svc.kit.me(token)
    except KitError as e:
        if e.status in (400, 401, 403, 404, 422):
            raise HTTPException(status_code=401, detail="Invalid login ID or password.")
        raise HTTPException(status_code=502, detail="College system is unavailable. Please try again.")
    old = current_session(request)
    svc.store.delete_session(old["sid"])            # rotate session id (prevents fixation)
    sid = svc.store.create_session()
    svc.store.set_session_user(sid, token, user)    # JWT stays server-side only
    set_session_cookie(response, sid, svc.settings)
    request.state.session_replaced = True
    # Warm the cache in the background (attendance, calendar, timetable...) so the first answer is fast.
    ctx = await tool_context(svc, {"sid": sid, "kit_token": token, "user": user})
    task = asyncio.create_task(prefetch_user_data(ctx))
    _background.add(task)
    task.add_done_callback(_background.discard)
    return {"user": public_user(user)}


@router.post("/logout")
async def logout(request: Request, response: Response, svc: Services = Depends(services)):
    svc.store.delete_session(current_session(request)["sid"])
    response.delete_cookie(SESSION_COOKIE, path="/")
    request.state.session_replaced = True
    return {"ok": True}
