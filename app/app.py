import datetime
import mimetypes
import shutil
import time
import glob
import os
import random
import string
import uuid
import re
import subprocess

import filetype
import timeout_decorator
from flask import Flask, jsonify, request, send_from_directory, Response
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from wand.exceptions import MissingDelegateError
from wand.image import Image
from werkzeug.middleware.proxy_fix import ProxyFix

import settings

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app)

CORS(app, origins=settings.ALLOWED_ORIGINS)
app.config["MAX_CONTENT_LENGTH"] = settings.MAX_SIZE_MB * 1024 * 1024
limiter = Limiter(get_remote_address, app=app, default_limits=[])

app.use_x_sendfile = True

@app.after_request
def after_request(resp):
    x_sendfile = resp.headers.get("X-Sendfile")
    if x_sendfile:
        resp.headers["X-Accel-Redirect"] = "/nginx/" + x_sendfile
        del resp.headers["X-Sendfile"]
    resp.headers["Referrer-Policy"] = "no-referrer-when-downgrade"
    return resp


class InvalidSize(Exception):
    pass

class CollisionError(Exception):
    pass

class InvalidFileTypeError(Exception):
    pass

def _get_size_from_string(size):
    try:
        size = int(size)
        if len(settings.VALID_SIZES) and size not in settings.VALID_SIZES:
            raise InvalidSize
    except ValueError:
        size = ""
    return size


def _clear_imagemagick_temp_files():
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


def _get_random_filename():
    random_string = _generate_random_filename()
    if settings.NAME_STRATEGY == "randomstr":
        file_exists = len(glob.glob(f"{settings.FILES_DIR}/{random_string}.*")) > 0
        if file_exists:
            return _get_random_filename()
    return random_string


def _generate_random_filename():
    if settings.NAME_STRATEGY == "uuidv4":
        return str(uuid.uuid4())
    if settings.NAME_STRATEGY == "randomstr":
        return "".join(
            random.choices(
                string.ascii_lowercase + string.digits + string.ascii_uppercase, k=5
            )
        )


def _resize_image(path, width, height):
    filename_without_extension, extension = os.path.splitext(path)

    with Image(filename=path) as src:
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

    try:
        resize(img, width, height)
    except timeout_decorator.TimeoutError:
        pass

    return img

@app.route("/liveness", methods=["GET"])
def liveness():
    return Response(status=200)

@app.route("/", methods=["POST"])
@limiter.limit(
    "".join(
        [
            f"{settings.MAX_UPLOADS_PER_DAY}/day;",
            f"{settings.MAX_UPLOADS_PER_HOUR}/hour;",
            f"{settings.MAX_UPLOADS_PER_MINUTE}/minute",
        ]
    )
)
def upload_file():
    _clear_imagemagick_temp_files()

    if "file" not in request.files:
        return jsonify(error="File is missing!"), 400

    file = request.files["file"]

    random_string = _get_random_filename()
    tmp_filepath = os.path.join("/tmp/", random_string)
    file.save(tmp_filepath)

    file_type = filetype.guess(tmp_filepath)
    if file_type is None:
        return jsonify(error="File type could not be determined!"), 400

    output_type = settings.OUTPUT_TYPE or file_type.extension
    output_filename = os.path.basename(tmp_filepath) + f".{output_type}"
    output_path = os.path.join(settings.FILES_DIR, output_filename)

    error = None

    try:
        if file_type.mime not in settings.ALLOWED_MIME_FILE_TYPES:
            raise InvalidFileTypeError
        if os.path.exists(output_path):
            raise CollisionError
        if file_type.mime not in settings.RESIZABLE_MIME_FILE_TYPE:
            shutil.move(tmp_filepath, output_path)
        else:
            with Image(filename=tmp_filepath) as img:
                img.strip()
                if output_type not in ["gif"]:
                    with img.sequence[0] as first_frame:
                        with Image(image=first_frame) as first_frame_img:
                            with first_frame_img.convert(output_type) as converted:
                                converted.save(filename=output_path)
                else:
                    with img.convert(output_type) as converted:
                        converted.save(filename=output_path)
    except (MissingDelegateError, InvalidFileTypeError):
        error = "Invalid Filetype"
    finally:
        if os.path.exists(tmp_filepath):
            os.remove(tmp_filepath)

    if error:
        return jsonify(error=error), 400

    return jsonify(filename=output_filename)

@app.route("/<string:filename>", methods=["DELETE"])
@limiter.exempt
def delete_image(filename):
    # check the name looks like a filename and 
    # need some mort protection
    if(filename) and (re.match("^[\w\d-]+\.[\w\d]+$", filename)):
        path = os.path.join(settings.FILES_DIR, filename)
        # dont allow to delete "."
        if (os.path.exists(path)) and (os.path.isfile(path)):
            os.remove(path)
    return Response(status=200)


@app.route("/<string:filename>")
@limiter.exempt
def get_file(filename):
    path = os.path.join(settings.FILES_DIR, filename)

    if os.path.isfile(path):

        file_type = filetype.guess(path)
        if file_type is None:
            return jsonify(error="File type could not be determined!"), 400
        
        if file_type.mime not in settings.RESIZABLE_MIME_FILE_TYPE:
            return send_from_directory(settings.FILES_DIR, filename)
        else:
            return _get_image(filename)
        
    return jsonify(error="File not found!"), 404

def _get_image(filename):
    width = request.args.get("w", "")
    height = request.args.get("h", "")

    path = os.path.join(settings.FILES_DIR, filename)

    if (width or height) and (os.path.isfile(path)):
        try:
            width = _get_size_from_string(width)
            height = _get_size_from_string(height)
        except InvalidSize:
            return (
                jsonify(error=f"size value must be one of {settings.VALID_SIZES}"),
                400,
            )

        filename_without_extension, extension_with_dot = os.path.splitext(filename)
        extension = extension_with_dot[1:]  # remove the dot from the extension
        dimensions = f"{width}x{height}"
        resized_filename = f"{filename_without_extension}_{dimensions}.{extension}"

        resized_path = os.path.join(settings.CACHE_DIR, resized_filename)

        if not os.path.isfile(resized_path) and (width or height):
            _clear_imagemagick_temp_files()
            resized_image = _resize_image(path, width, height)
            resized_image.strip()
            resized_image.save(filename=resized_path)
            resized_image.close()
        return send_from_directory(settings.CACHE_DIR, resized_filename)

    return send_from_directory(settings.FILES_DIR, filename)

@app.route("/metrics", methods=["GET"])
def metrics():
    metrics = {}
    for mime_type in settings.ALLOWED_MIME_FILE_TYPES:
        extension = mimetypes.guess_extension(mime_type)
        if extension:
            extension = extension[1:] # remove dot from extension
            ps = subprocess.Popen(f"find {settings.FILES_DIR} -type f -name '*.{extension}' | wc -l", shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            nbfiles = ps.communicate()[0].split()[0].decode('utf-8')
            size = subprocess.check_output([f'du -c {settings.FILES_DIR}/*.{extension} | tail -n 1 | cut -f 1'], shell=True).decode('utf-8').strip()
            metrics[mime_type] = {"count": nbfiles, "size": size}

    metrics_str = ""
    for mime_type, data in metrics.items():
        extension = mimetypes.guess_extension(mime_type)
        metrics_str += f'directory_size{{service="imgpush", extension="{extension}", mime_type="{mime_type}", directory="{settings.FILES_DIR}"}} {data["size"]}\n'
        metrics_str += f'directory_count{{service="imgpush", extension="{extension}", mime_type="{mime_type}", directory="{settings.FILES_DIR}"}} {data["count"]}\n'

    return metrics_str

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
