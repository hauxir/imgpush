import base64
import os
import secrets
import tempfile
import urllib.request
from typing import Any, Optional

import aiofiles
import filetype
import imgpush
import settings
import video
from fastapi import FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from limits import parse as parse_limit
from limits.storage import MemoryStorage
from limits.strategies import FixedWindowRateLimiter
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware
from storage import get_cache_storage, get_image_storage

app = FastAPI(openapi_url=None)

BLACK_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)

# Rate limiter
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter

# Rate limiter for failed API key attempts
_auth_limiter = FixedWindowRateLimiter(MemoryStorage())
_failed_auth_limit = parse_limit(f"{settings.MAX_API_KEY_ATTEMPTS_PER_MINUTE}/minute")


def check_auth(request: Request, authorization: Optional[str]) -> None:
    """Validate Bearer token authentication with rate limiting on failures."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=403, detail="Authorization required")

    token = authorization[7:]
    if settings.API_KEY is None or not secrets.compare_digest(token, settings.API_KEY):
        client_ip = get_remote_address(request)
        if not _auth_limiter.hit(_failed_auth_limit, client_ip):
            raise HTTPException(status_code=429, detail="Too many failed attempts")
        raise HTTPException(status_code=403, detail="Invalid API key")


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(content={"detail": "Rate limit exceeded"}, status_code=429)


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(content={"detail": "Internal server error"}, status_code=500)


# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


class HeaderMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Any) -> Response:
        response: Response = await call_next(request)
        x_sendfile = response.headers.get("X-Sendfile")
        if x_sendfile:
            response.headers["X-Accel-Redirect"] = "/nginx/" + x_sendfile
            del response.headers["X-Sendfile"]
        response.headers["Referrer-Policy"] = "no-referrer-when-downgrade"
        return response


app.add_middleware(HeaderMiddleware)


@app.get("/", response_class=HTMLResponse)
def root() -> str:
    if settings.HIDE_UPLOAD_FORM:
        return ""
    return """
<form action="/" method="post" enctype="multipart/form-data">
    <input type="file" name="file" id="file">
    <br>
    <label><input type="checkbox" name="remove_bg" value="true"> Remove background</label>
    <label><input type="checkbox" name="autocrop" value="true"> Autocrop</label>
    <br>
    <input type="submit" value="Upload" name="submit">
</form>
"""


@app.get("/liveness")
def liveness() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/")
@limiter.limit(
    f"{settings.MAX_UPLOADS_PER_DAY}/day;{settings.MAX_UPLOADS_PER_HOUR}/hour;{settings.MAX_UPLOADS_PER_MINUTE}/minute"
)
async def upload_image(
    request: Request,
    file: Optional[UploadFile] = File(default=None),
    remove_bg: Optional[str] = Form(default=None),
    autocrop: Optional[str] = Form(default=None),
    authorization: Optional[str] = Header(default=None),
) -> dict[str, str]:
    if settings.API_KEY and settings.REQUIRE_API_KEY_FOR_UPLOAD:
        check_auth(request, authorization)

    imgpush.clear_imagemagick_temp_files()

    is_svg = False

    random_string = imgpush.get_random_filename()
    tmp_filepath = os.path.join("/tmp", random_string)

    if file is not None and file.filename:
        is_svg = file.filename.endswith(".svg")
        async with aiofiles.open(tmp_filepath, "wb") as f:
            content = await file.read()
            await f.write(content)
    else:
        # Check for JSON body with URL
        try:
            body = await request.json()
            if "url" in body:
                urllib.request.urlretrieve(body["url"], tmp_filepath)
            else:
                raise HTTPException(status_code=400, detail="File is missing!")
            # Pick up remove_bg/autocrop from JSON body
            if remove_bg is None:
                remove_bg = body.get("remove_bg")
            if autocrop is None:
                autocrop = body.get("autocrop")
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=400, detail="File is missing!")

    if imgpush.check_nudity_filter(tmp_filepath):
        os.remove(tmp_filepath)
        raise HTTPException(status_code=400, detail="Nudity not allowed")

    file_filetype = filetype.guess_extension(tmp_filepath)
    output_type = (settings.OUTPUT_TYPE or file_filetype or "").replace(".", "")

    should_remove_bg = remove_bg in ("true", True) and settings.ALLOW_REMOVE_BG and not is_svg and file_filetype not in ("mp4",)
    should_autocrop = autocrop in ("true", True)

    if should_remove_bg:
        try:
            imgpush.remove_background(tmp_filepath, autocrop=should_autocrop)
        except Exception as exc:
            if os.path.exists(tmp_filepath):
                os.remove(tmp_filepath)
            raise HTTPException(status_code=400, detail=f"Background removal failed: {exc}")

    if should_remove_bg and not settings.OUTPUT_TYPE and output_type not in ("png", "webp"):
        output_type = "png"

    if file_filetype == "mp4":
        if not settings.ALLOW_VIDEO:
            os.remove(tmp_filepath)
            raise HTTPException(status_code=400, detail="Video uploads are not allowed")
        output_type = file_filetype
        if video.check_video_duration(tmp_filepath):
            os.remove(tmp_filepath)
            raise HTTPException(
                status_code=400,
                detail=f"Video exceeds maximum duration of {settings.MAX_VIDEO_DURATION} seconds",
            )
        if video.check_video_nudity_filter(tmp_filepath):
            os.remove(tmp_filepath)
            raise HTTPException(status_code=400, detail="Nudity not allowed")
    elif is_svg:
        output_type = "svg"

    output_filename = os.path.basename(tmp_filepath) + f".{output_type}"

    error = imgpush.process_image(tmp_filepath, output_filename, output_type, is_svg)

    if error:
        raise HTTPException(status_code=400, detail=error)

    return {"filename": output_filename}


@app.delete("/{filename:path}")
def delete_image(
    request: Request,
    filename: str,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, str]:
    if not settings.API_KEY or not settings.REQUIRE_API_KEY_FOR_DELETE:
        raise HTTPException(status_code=403, detail="Delete endpoint is disabled")

    check_auth(request, authorization)

    try:
        cached_deleted = imgpush.delete_image(filename)
    except imgpush.PathTraversalError:
        raise HTTPException(status_code=400, detail="Invalid filename")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")

    return {"status": "deleted", "cached_files_removed": str(cached_deleted)}


@app.get("/{filename:path}")
def get_image(
    filename: str,
    w: str = Query(default=""),
    h: str = Query(default=""),
) -> Response:
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    image_storage = get_image_storage()
    cache_storage = get_cache_storage()

    if not image_storage.file_exists(filename):
        return Response(content=BLACK_PIXEL_PNG, media_type="image/png", status_code=404)

    filename_without_extension, extension = os.path.splitext(filename)

    if (w or h) and extension not in (".mp4", ".svg"):
        try:
            width = imgpush.get_size_from_string(w)
            height = imgpush.get_size_from_string(h)
        except imgpush.InvalidSizeError:
            raise HTTPException(
                status_code=400,
                detail=f"size value must be one of {settings.VALID_SIZES}",
            )

        dimensions = f"{width}x{height}"
        resized_filename = filename_without_extension + f"_{dimensions}{extension}"

        if not cache_storage.file_exists(resized_filename) and (width or height):
            imgpush.clear_imagemagick_temp_files()
            source_path = image_storage.get_local_path(filename)
            fd, tmp_resized = tempfile.mkstemp(suffix=extension, dir="/tmp")
            os.close(fd)
            try:
                imgpush.resize_image(source_path, width, height, tmp_resized)
                cache_storage.upload_from_path(tmp_resized, resized_filename)
            finally:
                if os.path.exists(tmp_resized):
                    os.remove(tmp_resized)

        resized_path = cache_storage.get_local_path(resized_filename)
        return FileResponse(resized_path, headers={"X-Sendfile": resized_path})

    local_path = image_storage.get_local_path(filename)
    return FileResponse(local_path, headers={"X-Sendfile": local_path})
