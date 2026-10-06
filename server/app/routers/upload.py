import base64
import httpx
from fastapi import APIRouter, UploadFile, File, HTTPException

from app.services.llm_config import PROVIDER_MUAPI
from app.services.llm_service import current_llm_config

router = APIRouter(prefix="/api/v1", tags=["upload"])

ALLOWED_IMAGE_TYPES = {
    "image/jpeg", "image/png", "image/webp", "image/gif", "image/avif"
}

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}

@router.post("/upload")
async def upload_image_file(file: UploadFile = File(...)):
    """
    Direct file upload endpoint for images.

    OpenAI-compatible servers accept images inline as data URLs, so the image
    stays on this machine. MUAPI needs a hosted URL, so for that provider the
    file is first sent to its `/upload_file` endpoint.
    """
    content_type = file.content_type or ""
    filename = file.filename or ""
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    # Validate image ONLY constraint
    if content_type not in ALLOWED_IMAGE_TYPES and ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Only image files (JPEG, PNG, WEBP, GIF, AVIF) are allowed."
        )

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded image file is empty.")

    config = current_llm_config()

    if config.provider == PROVIDER_MUAPI and config.api_key and not config.api_key.startswith("mock_"):
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                files_payload = {"file": (filename or "image.png", file_bytes, content_type or "image/png")}
                headers = {"x-api-key": config.api_key}

                res = await client.post(f"{config.base_url}/upload_file", files=files_payload, headers=headers)
                if res.status_code == 200:
                    res_data = res.json()
                    hosted_url = res_data.get("url") or res_data.get("file_url") or res_data.get("link")
                    if hosted_url:
                        return {"url": hosted_url, "filename": filename}
        except Exception as err:
            print(f"MUAPI upload_file dispatch notice: {err}")

    # Inline data URL: used directly by OpenAI-compatible vision endpoints and
    # as the local preview fallback.
    b64_str = base64.b64encode(file_bytes).decode("utf-8")
    data_url = f"data:{content_type or 'image/png'};base64,{b64_str}"
    return {"url": data_url, "filename": filename}
