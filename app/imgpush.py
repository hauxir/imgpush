import datetime
import gc
import glob
import os
import random
import shutil
import string
import time
import uuid
from typing import Optional, Union

import settings
from PIL import Image as PILImage
from wand.exceptions import MissingDelegateError
from wand.image import Image

if settings.ALLOW_REMOVE_BG:
    try:
        from rembg import remove as rembg_remove
    except ImportError:
        raise ImportError("ALLOW_REMOVE_BG is enabled but rembg is not installed. Install with: pip install rembg[cpu]")
else:
    rembg_remove = None

if settings.NUDE_FILTER_MAX_THRESHOLD:
    from nudenet import NudeClassifier

    nude_classifier = NudeClassifier()
else:
    nude_classifier = None


AUTOCROP_ALPHA_THRESHOLD = 10


class InvalidSizeError(Exception):
    pass


class CollisionError(Exception):
    pass


class PathTraversalError(Exception):
    pass


SHARD_SEGMENT_LEN = 2
SHARD_DEPTH = 2


def _is_safe_filename(filename: str) -> bool:
    """Reject anything that isn't a plain basename (blocks path traversal)."""
    return bool(filename) and filename not in (".", "..") and filename == os.path.basename(filename)


def _shard_segments(filename: str) -> list[str]:
    key = filename[: SHARD_SEGMENT_LEN * SHARD_DEPTH]
    return [key[i * SHARD_SEGMENT_LEN : (i + 1) * SHARD_SEGMENT_LEN] for i in range(SHARD_DEPTH)]


def resolve_path(base_dir: str, filename: str, create_parents: bool = False) -> str:
    """Map a filename to its on-disk path under base_dir, respecting SHARD_STORAGE."""
    if not _is_safe_filename(filename):
        raise PathTraversalError("Invalid filename")
    if not settings.SHARD_STORAGE:
        return os.path.join(base_dir, filename)
    path = os.path.join(base_dir, *_shard_segments(filename), filename)
    if create_parents:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def resolve_existing_path(base_dir: str, filename: str) -> Optional[str]:
    """Return the actual path of filename under base_dir, whether flat or sharded.

    Supports mixed-era installs where some files exist flat and others sharded.
    Returns None for invalid filenames (path traversal attempts) or misses.
    """
    if not _is_safe_filename(filename):
        return None
    flat = os.path.join(base_dir, filename)
    if os.path.isfile(flat):
        return flat
    if settings.SHARD_STORAGE:
        sharded = os.path.join(base_dir, *_shard_segments(filename), filename)
        if os.path.isfile(sharded):
            return sharded
    return None


def get_size_from_string(size: str) -> Union[int, str]:
    try:
        size_int = int(size)
        if len(settings.VALID_SIZES) and size_int not in settings.VALID_SIZES:
            raise InvalidSizeError
        return size_int
    except ValueError:
        return ""


def clear_imagemagick_temp_files() -> None:
    """
    A bit of a hacky solution to prevent exhausting the cache ImageMagick uses on disk.
    It works by checking for imagemagick cache files under /tmp/
    and removes those that are older than settings.MAX_TMP_FILE_AGE in seconds.
    """
    imagemagick_temp_files = glob.glob("/tmp/magick-*")
    for filepath in imagemagick_temp_files:
        modified = datetime.datetime.strptime(
            time.ctime(os.path.getmtime(filepath)),
            "%a %b %d %H:%M:%S %Y",
        )
        diff = datetime.datetime.now() - modified
        seconds = diff.seconds
        if seconds > settings.MAX_TMP_FILE_AGE:
            os.remove(filepath)


def get_random_filename() -> str:
    random_string = generate_random_filename()
    if settings.NAME_STRATEGY == "randomstr":
        search_dir = os.path.dirname(resolve_path(settings.IMAGES_DIR, random_string + ".x"))
        file_exists = len(glob.glob(f"{search_dir}/{random_string}.*")) > 0
        if file_exists:
            return get_random_filename()
    return random_string


def generate_random_filename() -> str:
    if settings.NAME_STRATEGY == "uuidv4":
        return str(uuid.uuid4())
    elif settings.NAME_STRATEGY == "randomstr":
        return "".join(random.choices(string.ascii_lowercase + string.digits + string.ascii_uppercase, k=5))
    return ""


def resize_image(path: str, width: Union[int, str], height: Union[int, str], output_path: str) -> None:
    _, extension = os.path.splitext(path)

    is_animated_webp = False

    with Image(filename=path) as src:
        is_animated_webp = extension == ".webp" and len(src.sequence) > 1

        if is_animated_webp:
            img = src.convert("gif")
        else:
            img = src.clone()

    try:
        current_aspect_ratio = img.width / img.height

        # Convert to integers if they're strings or empty
        width_int = int(width) if width else 0
        height_int = int(height) if height else 0

        if not width_int:
            width_int = int(current_aspect_ratio * height_int)

        if not height_int:
            height_int = int(width_int / current_aspect_ratio)

        desired_aspect_ratio = width_int / height_int

        # Crop the image to fit the desired AR
        if desired_aspect_ratio > current_aspect_ratio:
            newheight = int(img.width / desired_aspect_ratio)
            img.crop(
                0,
                int((img.height / 2) - (newheight / 2)),
                width=img.width,
                height=newheight,
            )
        else:
            newwidth = int(img.height * desired_aspect_ratio)
            img.crop(
                int((img.width / 2) - (newwidth / 2)),
                0,
                width=newwidth,
                height=img.height,
            )

        img.sample(width_int, height_int)

        if is_animated_webp:
            with img.convert("webp") as converted:
                converted.strip()
                converted.save(filename=output_path)
        else:
            img.strip()
            img.save(filename=output_path)
    finally:
        img.close()
        gc.collect()


def check_nudity_filter(filepath: str) -> bool:
    """Check if image passes nudity filter"""
    if settings.NUDE_FILTER_MAX_THRESHOLD and nude_classifier is not None:
        unsafe_val = nude_classifier.classify(filepath).get(filepath, {}).get("unsafe", 0)
        return unsafe_val >= settings.NUDE_FILTER_MAX_THRESHOLD
    return False


def delete_image(filename: str) -> int:
    """Delete an image and all its cached resized versions.

    Returns the number of cached files deleted.
    Raises PathTraversalError if filename attempts directory traversal.
    """
    # Sanitize filename to prevent path traversal
    candidate = resolve_existing_path(settings.IMAGES_DIR, filename) or os.path.join(settings.IMAGES_DIR, filename)
    image_path = os.path.realpath(candidate)
    images_dir = os.path.realpath(settings.IMAGES_DIR)

    if not image_path.startswith(images_dir + os.sep):
        raise PathTraversalError("Invalid filename")

    if not os.path.isfile(image_path):
        raise FileNotFoundError(f"Image {filename} not found")

    os.remove(image_path)

    # Also sanitize cache path - escape glob special chars in filename
    safe_filename = os.path.basename(image_path)
    filename_without_ext, extension = os.path.splitext(safe_filename)
    variant_suffix = f"{glob.escape(filename_without_ext)}_*x*{glob.escape(extension)}"
    sharded_cache_dir = os.path.dirname(resolve_path(settings.CACHE_DIR, safe_filename))
    cache_patterns = [os.path.join(settings.CACHE_DIR, variant_suffix)]
    if sharded_cache_dir != settings.CACHE_DIR.rstrip(os.sep):
        cache_patterns.append(os.path.join(sharded_cache_dir, variant_suffix))
    cached_files = [p for pattern in cache_patterns for p in glob.glob(pattern)]

    for cached_file in cached_files:
        os.remove(cached_file)

    return len(cached_files)


def remove_background(filepath: str, autocrop: bool = False) -> None:
    """Remove background from image using rembg, optionally autocrop to content bounds."""
    assert rembg_remove is not None  # guaranteed by startup check
    with PILImage.open(filepath) as img:
        result = rembg_remove(img, force_return_bytes=False)
    if not isinstance(result, PILImage.Image):
        raise TypeError(f"rembg returned {type(result).__name__}, expected PIL Image")

    try:
        if autocrop:
            result = result.convert("RGBA")
            alpha = result.split()[3].point(lambda v: 0 if v <= AUTOCROP_ALPHA_THRESHOLD else 255)  # type: ignore[reportUnknownLambdaType]
            bbox = alpha.getbbox()
            if bbox:
                result = result.crop(bbox)

        result.save(filepath, format="PNG")
    finally:
        result.close()


def process_image(tmp_filepath: str, output_path: str, output_type: str, is_svg: bool = False) -> Optional[str]:
    """Process and save image with appropriate format conversion"""
    error = None

    try:
        if os.path.exists(output_path):
            raise CollisionError
        if output_type == "mp4":
            if settings.ALLOW_VIDEO:
                shutil.move(tmp_filepath, output_path)
            else:
                error = "Invalid Filetype"
        elif output_type == "svg":
            shutil.move(tmp_filepath, output_path)
        else:
            with Image(filename=tmp_filepath) as img:
                img.strip()
                if output_type not in ["gif", "webp"]:
                    # Extract first frame for non-animated formats
                    first_frame = img.sequence[0]  # type: ignore
                    with Image(image=first_frame) as first_frame_img, first_frame_img.convert(output_type) as converted:
                        converted.save(filename=output_path)
                else:
                    # Coalesce frames to ensure consistent dimensions for animated images
                    # Required for WebP which doesn't support variable frame sizes
                    img.coalesce()
                    with img.convert(output_type) as converted:
                        converted.save(filename=output_path)
    except MissingDelegateError:
        error = "Invalid Filetype"
    finally:
        if os.path.exists(tmp_filepath):
            os.remove(tmp_filepath)
        gc.collect()

    return error
