from fastapi import APIRouter, Depends, HTTPException, Request

from ..services import Services, current_session, owner_key, services

router = APIRouter(prefix="/api/conversations", tags=["history"])


@router.get("")
async def list_conversations(request: Request, svc: Services = Depends(services)):
    return {"conversations": svc.store.list_conversations(owner_key(current_session(request)))}


@router.get("/{conversation_id}")
async def get_conversation(conversation_id: str, request: Request, svc: Services = Depends(services)):
    conv = svc.store.get_conversation(conversation_id, owner_key(current_session(request)))
    if not conv:  # also hides other users' conversations
        raise HTTPException(status_code=404, detail="Conversation not found.")
    conv.pop("pending_action", None)
    return {"conversation": conv, "messages": svc.store.get_messages(conversation_id)}
