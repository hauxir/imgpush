import os
import secrets
import urllib.request
from typing import Any, Optional

import aiofiles
import filetype
import imgpush
import settings
import video
from fastapi import FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from limits import parse as parse_limit
from limits.storage import MemoryStorage
from limits.strategies import FixedWindowRateLimiter
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware

app = FastAPI(openapi_url=None)

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
async def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> Response:
    return Response(content="Rate limit exceeded", status_code=429)


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
    <input type="submit" value="Upload" name="submit">
</form>
"""


@app.get("/liveness")
def liveness() -> Response:
    return Response(status_code=200)


@app.post("/")
@limiter.limit(
    f"{settings.MAX_UPLOADS_PER_DAY}/day;{settings.MAX_UPLOADS_PER_HOUR}/hour;{settings.MAX_UPLOADS_PER_MINUTE}/minute"
)
async def upload_image(
    request: Request,
    file: Optional[UploadFile] = File(default=None),
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
        except Exception:
            raise HTTPException(status_code=400, detail="File is missing!")

    if imgpush.check_nudity_filter(tmp_filepath):
        os.remove(tmp_filepath)
        raise HTTPException(status_code=400, detail="Nudity not allowed")

    file_filetype = filetype.guess_extension(tmp_filepath)
    output_type = (settings.OUTPUT_TYPE or file_filetype or "").replace(".", "")

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
    output_path = os.path.join(settings.IMAGES_DIR, output_filename)

    error = imgpush.process_image(tmp_filepath, output_path, output_type, is_svg)

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
) -> FileResponse:
    path = os.path.join(settings.IMAGES_DIR, filename)

    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="File not found")

    filename_without_extension, extension = os.path.splitext(filename)

    if (w or h) and os.path.isfile(path) and extension != ".mp4":
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

        resized_path = os.path.join(settings.CACHE_DIR, resized_filename)

        if not os.path.isfile(resized_path) and (width or height):
            imgpush.clear_imagemagick_temp_files()
            resized_image = imgpush.resize_image(path, width, height)
            resized_image.strip()
            resized_image.save(filename=resized_path)
            resized_image.close()
        return FileResponse(resized_path, headers={"X-Sendfile": resized_path})

    return FileResponse(path, headers={"X-Sendfile": path})
