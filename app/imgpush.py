import contextlib
import datetime
import glob
import os
import random
import shutil
import string
import time
import uuid
from typing import Optional, Union

import settings
import timeout_decorator
from wand.exceptions import MissingDelegateError
from wand.image import Image

if settings.NUDE_FILTER_MAX_THRESHOLD:
    from nudenet import NudeClassifier
    nude_classifier = NudeClassifier()
else:
    nude_classifier = None


class InvalidSizeError(Exception):
    pass


class CollisionError(Exception):
    pass


class PathTraversalError(Exception):
    pass


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
            time.ctime(os.path.getmtime(filepath)), "%a %b %d %H:%M:%S %Y",
        )
        diff = datetime.datetime.now() - modified
        seconds = diff.seconds
        if seconds > settings.MAX_TMP_FILE_AGE:
            os.remove(filepath)


def get_random_filename() -> str:
    random_string = generate_random_filename()
    if settings.NAME_STRATEGY == "randomstr":
        file_exists = len(glob.glob(f"{settings.IMAGES_DIR}/{random_string}.*")) > 0
        if file_exists:
            return get_random_filename()
    return random_string


def generate_random_filename() -> str:
    if settings.NAME_STRATEGY == "uuidv4":
        return str(uuid.uuid4())
    elif settings.NAME_STRATEGY == "randomstr":
        return "".join(
            random.choices(
                string.ascii_lowercase + string.digits + string.ascii_uppercase, k=5
            )
        )
    return ""


def resize_image(path: str, width: Union[int, str], height: Union[int, str]) -> Image:
    _, extension = os.path.splitext(path)

    is_animated_webp = False

    with Image(filename=path) as src:
        is_animated_webp = extension == ".webp" and len(src.sequence) > 1

        if is_animated_webp:
            img = src.convert("gif")
        else:
            img = src.clone()

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
            int((img.width / 2) - (newwidth / 2)), 0, width=newwidth, height=img.height,
        )

    @timeout_decorator.timeout(settings.RESIZE_TIMEOUT)
    def resize(img: Image, width: int, height: int) -> None:
        img.sample(width, height)

    with contextlib.suppress(timeout_decorator.TimeoutError):
        resize(img, width_int, height_int)

    if is_animated_webp:
        converted = img.convert("webp")
        img.close()
        return converted

    return img


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
    image_path = os.path.realpath(os.path.join(settings.IMAGES_DIR, filename))
    images_dir = os.path.realpath(settings.IMAGES_DIR)

    if not image_path.startswith(images_dir + os.sep):
        raise PathTraversalError("Invalid filename")

    if not os.path.isfile(image_path):
        raise FileNotFoundError(f"Image {filename} not found")

    os.remove(image_path)

    # Also sanitize cache path - escape glob special chars in filename
    safe_filename = os.path.basename(image_path)
    filename_without_ext, extension = os.path.splitext(safe_filename)
    cache_pattern = os.path.join(
        settings.CACHE_DIR, f"{glob.escape(filename_without_ext)}_*x*{glob.escape(extension)}"
    )
    cached_files = glob.glob(cache_pattern)

    for cached_file in cached_files:
        os.remove(cached_file)

    return len(cached_files)


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
                    with Image(image=first_frame) as first_frame_img, \
                         first_frame_img.convert(output_type) as converted:
                        converted.save(filename=output_path)
                else:
                    with img.convert(output_type) as converted:
                        converted.save(filename=output_path)
    except MissingDelegateError:
        error = "Invalid Filetype"
    finally:
        if os.path.exists(tmp_filepath):
            os.remove(tmp_filepath)

    return error
