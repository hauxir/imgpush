import os
import urllib.request
from typing import Any, Union

import filetype
import imgpush
import settings
from flask import Flask, Response, jsonify, request, send_from_directory
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app)

CORS(app, origins=settings.ALLOWED_ORIGINS)
app.config["MAX_CONTENT_LENGTH"] = settings.MAX_SIZE_MB * 1024 * 1024
limiter = Limiter(get_remote_address, app=app, default_limits=[])

app.config["USE_X_SENDFILE"] = True


@app.after_request
def after_request(resp: Any) -> Any:
    x_sendfile = resp.headers.get("X-Sendfile")
    if x_sendfile:
        resp.headers["X-Accel-Redirect"] = "/nginx/" + x_sendfile
        del resp.headers["X-Sendfile"]
    resp.headers["Referrer-Policy"] = "no-referrer-when-downgrade"
    return resp




@app.route("/", methods=["GET"])
def root() -> str:
    return """
<form action="/" method="post" enctype="multipart/form-data">
    <input type="file" name="file" id="file">
    <input type="submit" value="Upload" name="submit">
</form>
"""


@app.route("/liveness", methods=["GET"])
def liveness() -> Response:
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
def upload_image() -> Union[tuple[Any, int], Any]:
    imgpush.clear_imagemagick_temp_files()

    is_svg = False

    random_string = imgpush.get_random_filename()
    tmp_filepath = os.path.join("/tmp", random_string)

    if "file" in request.files:
        file = request.files["file"]
        is_svg = file.filename.endswith(".svg")
        file.save(tmp_filepath)
    elif request.json and "url" in request.json:
        urllib.request.urlretrieve(request.json["url"], tmp_filepath)
    else:
        return jsonify(error="File is missing!"), 400

    if imgpush.check_nudity_filter(tmp_filepath):
        os.remove(tmp_filepath)
        return jsonify(error="Nudity not allowed"), 400

    file_filetype = filetype.guess_extension(tmp_filepath)
    output_type = (settings.OUTPUT_TYPE or file_filetype).replace(".", "")

    if file_filetype == "mp4":
        output_type = file_filetype
    elif is_svg:
        output_type = "svg"

    output_filename = os.path.basename(tmp_filepath) + f".{output_type}"
    output_path = os.path.join(settings.IMAGES_DIR, output_filename)

    error = imgpush.process_image(tmp_filepath, output_path, output_type, is_svg)

    if error:
        return jsonify(error=error), 400

    return jsonify(filename=output_filename)


@app.route("/<string:filename>")
@limiter.exempt
def get_image(filename: str) -> Union[tuple[Any, int], Response]:
    width = request.args.get("w", "")
    height = request.args.get("h", "")

    path = os.path.join(settings.IMAGES_DIR, filename)

    filename_without_extension, extension = os.path.splitext(filename)

    if (width or height) and (os.path.isfile(path)) and extension != ".mp4":
        try:
            width = imgpush.get_size_from_string(width)
            height = imgpush.get_size_from_string(height)
        except imgpush.InvalidSizeError:
            return (
                jsonify(error=f"size value must be one of {settings.VALID_SIZES}"),
                400,
            )

        dimensions = f"{width}x{height}"
        resized_filename = filename_without_extension + f"_{dimensions}.{extension}"

        resized_path = os.path.join(settings.CACHE_DIR, resized_filename)

        if not os.path.isfile(resized_path) and (width or height):
            imgpush.clear_imagemagick_temp_files()
            resized_image = imgpush.resize_image(path, width, height)
            resized_image.strip()
            resized_image.save(filename=resized_path)
            resized_image.close()
        return send_from_directory(settings.CACHE_DIR, resized_filename)

    return send_from_directory(settings.IMAGES_DIR, filename)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
