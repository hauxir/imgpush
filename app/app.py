import glob
import os
import random
import string
import uuid

import filetype
import settings
from flask import Flask, jsonify, request, send_from_directory, Response
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from wand.exceptions import MissingDelegateError
from wand.image import Image
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app)

CORS(app, origins=settings.ALLOWED_ORIGINS)
app.config["MAX_CONTENT_LENGTH"] = settings.MAX_SIZE_MB * 1024 * 1024
limiter = Limiter(app, key_func=get_remote_address, default_limits=[])


class InvalidSize(Exception):
    pass


def _get_size_from_string(size):
    try:
        size = int(size)
        if len(settings.VALID_SIZES) and size not in settings.VALID_SIZES:
            raise InvalidSize
    except ValueError:
        size = ""
    return size


def _get_random_filename():
    random_string = _generate_random_filename()
    file_exists = len(glob.glob(f"{settings.IMAGES_DIR}/{random_string}.*")) > 0
    if file_exists:
        return _get_random_filename()
    return random_string

def _generate_random_filename():
    if settings.NAME_STRATEGY == settings.NAME_STRATEGY_UUIDV4:
        return str(uuid.uuid4())
    if settings.NAME_STRATEGY == settings.NAME_STRATEGY_RANDOMSTR:
        return "".join(random.choices(string.ascii_lowercase + string.digits + string.ascii_uppercase, k=5))

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
                int((img.width / 2) - (newwidth / 2)),
                0,
                width=newwidth,
                height=img.height,
            )

        img.resize(width, height)

        return img


@app.route("/", methods=["GET"])
def root():
    return """
<form action="/" method="post" enctype="multipart/form-data">
    <input type="file" name="file" id="file">
    <input type="submit" value="Upload" name="submit">
</form>
"""

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
def upload_image():
    if "file" not in request.files:
        return jsonify(error="File is missing!"), 400

    file = request.files["file"]

    random_string = _get_random_filename()
    tmp_filepath = os.path.join("/tmp/", random_string)
    file.save(tmp_filepath)
    output_type = settings.OUTPUT_TYPE or filetype.guess_extension(tmp_filepath)
    error = None

    try:
        with Image(filename=tmp_filepath) as img:
            img.strip()
            with img.convert(output_type) as converted:
                output_filename = os.path.basename(tmp_filepath) + f".{output_type}"
                output_path = os.path.join(settings.IMAGES_DIR, output_filename)
                if output_type not in ["gif"]:
                    converted.sequence = [converted.sequence[0]]
                converted.save(filename=output_path)
    except MissingDelegateError:
        error = "Invalid Filetype"
    finally:
        if os.path.exists(tmp_filepath):
            os.remove(tmp_filepath)

    if error:
        return jsonify(error=error), 400

    return jsonify(filename=output_filename)


@app.route("/<string:filename>")
@limiter.exempt
def get_image(filename):
    width = request.args.get("w", "")
    height = request.args.get("h", "")

    path = os.path.join(settings.IMAGES_DIR, filename)

    if (width or height) and (os.path.isfile(path)):
        try:
            width = _get_size_from_string(width)
            height = _get_size_from_string(height)
        except InvalidSize:
            return (
                jsonify(error=f"size value must be one of {settings.VALID_SIZES}"),
                400,
            )

        filename_without_extension, extension = os.path.splitext(filename)
        dimensions = f"{width}x{height}"
        resized_filename = filename_without_extension + f"_{dimensions}.{extension}"

        resized_path = os.path.join(settings.CACHE_DIR, resized_filename)

        if not os.path.isfile(resized_path) and (width or height):
            resized_image = _resize_image(path, width, height)
            resized_image.strip()
            resized_image.save(filename=resized_path)
            resized_image.close()

        return send_from_directory(settings.CACHE_DIR, resized_filename)

    return send_from_directory(settings.IMAGES_DIR, filename)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
