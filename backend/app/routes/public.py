import time

from fastapi import APIRouter, Depends, HTTPException

from ..kit_client import KitError
from ..services import Services, college_today, services

router = APIRouter(prefix="/api", tags=["public"])


@router.get("/public/college")
async def college(svc: Services = Depends(services)):
    try:
        info = svc.cache.get("pub:college") or await svc.kit.college()
        svc.cache.set("pub:college", info, 600)
        courses = svc.cache.get("pub:courses") or await svc.kit.courses()
        svc.cache.set("pub:courses", courses, 300)
        today = await college_today(svc)
    except KitError:
        raise HTTPException(status_code=502, detail="College system is unavailable.")
    return {"college": info, "courses": courses, "today": today}


@router.get("/health")
async def health(svc: Services = Depends(services)):
    try:
        await svc.kit.today()
        kit_ok = True
    except KitError:
        kit_ok = False
    return {
        "status": "ok",
        "time": time.time(),
        "college_api": kit_ok,
        "documents": {"loaded": len(svc.kb.chunks), "last_refresh_ok": svc.kb.last_refresh_ok,
                      "documents": svc.kb.catalogue()},
        "llm": svc.agent.llm.status,
        "speech": svc.speech.status,
    }
