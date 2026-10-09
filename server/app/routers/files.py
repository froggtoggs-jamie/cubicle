"""Serve files a bot has shared into the chat."""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from app.services.file_share import FileShareError, resolve_shared_file

router = APIRouter(prefix="/api/v1/files", tags=["files"])


@router.get("/download")
async def download_file(
    source: str = Query(...),
    path: str = Query(...),
    bot_id: str = Query(""),
    inline: bool = Query(False),
):
    """Download (or, with inline=1, preview) a shared file.

    The same confinement rules as sharing apply, so a stale card for a file
    that has moved outside the root, or been deleted, gets a 404.
    """
    try:
        shared = resolve_shared_file(source, path, bot_id or None)
    except FileShareError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return FileResponse(
        shared.absolute,
        media_type=shared.mime,
        filename=shared.name,
        content_disposition_type="inline" if inline else "attachment",
        headers={"Cache-Control": "no-store"},
    )
