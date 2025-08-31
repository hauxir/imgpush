import contextlib
import datetime
import glob
import os
import random
import shutil
import string
import time
import uuid

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


def get_size_from_string(size):
    try:
        size = int(size)
        if len(settings.VALID_SIZES) and size not in settings.VALID_SIZES:
            raise InvalidSizeError
    except ValueError:
        size = ""
    return size


def clear_imagemagick_temp_files():
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
    if settings.NAME_STRATEGY == "randomstr":
        return "".join(
            random.choices(
                string.ascii_lowercase + string.digits + string.ascii_uppercase, k=5
            )
        )
    return ""


def resize_image(path, width, height):
    _, extension = os.path.splitext(path)

    is_animated_webp = False

    with Image(filename=path) as src:
        is_animated_webp = extension == ".webp" and len(src.sequence) > 1

        if is_animated_webp:
            img = src.convert("gif")
        else:
            img = src.clone()

    current_aspect_ratio = img.width / img.height

    if not width:
        width = int(current_aspect_ratio * height)

    if not height:
        height = int(width / current_aspect_ratio)

    desired_aspect_ratio = width / height

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
    def resize(img, width, height):
        img.sample(width, height)

    with contextlib.suppress(timeout_decorator.TimeoutError):
        resize(img, width, height)

    if is_animated_webp:
        converted = img.convert("webp")
        img.close()
        return converted

    return img


def check_nudity_filter(filepath):
    """Check if image passes nudity filter"""
    if settings.NUDE_FILTER_MAX_THRESHOLD:
        unsafe_val = nude_classifier.classify(filepath).get(filepath, {}).get("unsafe", 0)
        return unsafe_val >= settings.NUDE_FILTER_MAX_THRESHOLD
    return False


def process_image(tmp_filepath, output_path, output_type, is_svg=False):
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
