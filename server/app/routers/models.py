from fastapi import APIRouter, Query

from app.schemas.contracts import ModelCatalog
from app.services import llm_service

router = APIRouter(prefix="/api/v1/models", tags=["models"])


@router.get("", response_model=ModelCatalog)
async def list_models(refresh: bool = Query(False)):
    """Return the model catalog for the configured provider.

    For OpenAI-compatible servers this is the live `/models` listing (cached
    briefly; pass `refresh=true` to bypass the cache). For MUAPI it is the
    static registry. When the server cannot be reached the catalog still
    contains the configured default model and carries the error text.
    """
    return await llm_service.list_models(refresh=refresh)
