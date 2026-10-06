"""Bot personas: create, edit, archive, and delete."""

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.schemas.contracts import Bot
from app.services import llm_service
from app.services.computer_provider import ComputerProviderError, computer_id_for_bot, computer_provider
from app.services.storage_service import storage_service
from app.services.turn_manager import turn_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/bots", tags=["bots"])

MAX_NAME = 80
MAX_SHORT = 200
MAX_PROMPT = 20_000
# Only these fields can be changed from the client; ids, counters, and
# timestamps are the server's.
EDITABLE_FIELDS = {"name", "role", "description", "avatar", "model", "accent_color", "system_prompt", "tools", "pinned"}


class BotInput(BaseModel):
    name: str = Field(default="", max_length=MAX_NAME)
    role: str = Field(default="", max_length=MAX_SHORT)
    description: str = Field(default="", max_length=MAX_SHORT)
    avatar: str = Field(default="", max_length=16)
    model: Optional[str] = Field(default=None, max_length=200)
    accent_color: str = Field(default="", max_length=32)
    system_prompt: str = Field(default="", max_length=MAX_PROMPT)
    tools: List[str] = Field(default_factory=list)


class BotUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=MAX_NAME)
    role: Optional[str] = Field(default=None, max_length=MAX_SHORT)
    description: Optional[str] = Field(default=None, max_length=MAX_SHORT)
    avatar: Optional[str] = Field(default=None, max_length=16)
    model: Optional[str] = Field(default=None, max_length=200)
    accent_color: Optional[str] = Field(default=None, max_length=32)
    system_prompt: Optional[str] = Field(default=None, max_length=MAX_PROMPT)
    tools: Optional[List[str]] = None
    pinned: Optional[bool] = None
    archived: Optional[bool] = None


def _find(bots: List[Dict[str, Any]], bot_id: str) -> int:
    for index, bot in enumerate(bots):
        if bot.get("id") == bot_id:
            return index
    raise HTTPException(status_code=404, detail="Bot not found")


@router.get("", response_model=List[Bot])
async def get_bots():
    return storage_service.get_bots()


@router.post("", response_model=Bot)
async def create_bot(bot_data: BotInput):
    name = bot_data.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="A bot needs a name.")
    model = (bot_data.model or "").strip() or llm_service.current_llm_config().default_model
    bot = {
        "id": f"bot-{uuid.uuid4().hex[:6]}",
        "name": name,
        "role": bot_data.role.strip() or "AI Assistant",
        "description": bot_data.description.strip() or f"Custom agent running {model}.",
        "avatar": bot_data.avatar.strip() or "🤖",
        "model": model,
        "accent_color": bot_data.accent_color.strip() or "#3b82f6",
        "system_prompt": bot_data.system_prompt.strip() or f"You are {name}, a helpful AI assistant.",
        "tools": bot_data.tools,
        "pinned": False,
        "archived": False,
        "unread_count": 0,
        "created_at": datetime.now().isoformat(),
    }
    bots = storage_service.get_bots()
    bots.append(bot)
    storage_service.save_bots(bots)
    return bot


@router.put("/{bot_id}", response_model=Bot)
async def update_bot(bot_id: str, updates: BotUpdate):
    bots = storage_service.get_bots()
    index = _find(bots, bot_id)
    changes = updates.model_dump(exclude_none=True)
    if "name" in changes:
        changes["name"] = changes["name"].strip()
        if not changes["name"]:
            raise HTTPException(status_code=422, detail="A bot needs a name.")
    if "model" in changes and not changes["model"].strip():
        changes.pop("model")
    archived = changes.pop("archived", None)
    bots[index].update({key: value for key, value in changes.items() if key in EDITABLE_FIELDS})
    if archived is not None:
        bots[index]["archived"] = archived
        bots[index]["archived_at"] = datetime.now().isoformat() if archived else None
        if archived:
            # An archived bot should not keep working in the background.
            turn_manager.cancel(bot_id)
    storage_service.save_bots(bots)
    return bots[index]


@router.delete("/{bot_id}")
async def delete_bot(bot_id: str):
    """Remove a bot together with its transcript, running turn, and computer."""
    bots = storage_service.get_bots()
    index = _find(bots, bot_id)
    turn_manager.cancel(bot_id)
    try:
        await computer_provider.stop(computer_id_for_bot(bot_id))
    except ComputerProviderError:
        pass  # No computer, or none running: nothing to stop.
    except Exception as exc:  # The deletion must not fail because a container misbehaves.
        logger.warning("Could not stop the computer for %s during deletion: %s", bot_id, exc)
    deleted_messages = storage_service.delete_messages(bot_id)
    bots.pop(index)
    storage_service.save_bots(bots)
    return {"status": "ok", "deleted_id": bot_id, "deleted_messages": deleted_messages}
