import os
import tempfile

import pytest

# Check if ImageMagick is available (required for app import)
try:
    from wand.api import library  # noqa: F401
    IMAGEMAGICK_AVAILABLE = True
except ImportError:
    IMAGEMAGICK_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not IMAGEMAGICK_AVAILABLE,
    reason="ImageMagick not installed"
)


@pytest.fixture
def client():
    """Create a test client for the FastAPI app."""
    from fastapi.testclient import TestClient

    from app import app
    return TestClient(app)


@pytest.fixture
def temp_dirs(monkeypatch):
    """Create temporary directories for images and cache."""
    with tempfile.TemporaryDirectory() as images_dir, tempfile.TemporaryDirectory() as cache_dir:
        monkeypatch.setattr("settings.IMAGES_DIR", images_dir)
        monkeypatch.setattr("settings.CACHE_DIR", cache_dir)
        yield {"images": images_dir, "cache": cache_dir}


class TestRootEndpoint:
    def test_get_returns_upload_form(self, client, monkeypatch):
        monkeypatch.setattr("settings.HIDE_UPLOAD_FORM", False)
        response = client.get("/")
        assert response.status_code == 200
        assert "<form" in response.text
        assert 'enctype="multipart/form-data"' in response.text

    def test_get_returns_empty_when_form_hidden(self, client, monkeypatch):
        monkeypatch.setattr("settings.HIDE_UPLOAD_FORM", True)
        response = client.get("/")
        assert response.status_code == 200
        assert response.text == ""


class TestLivenessEndpoint:
    def test_returns_200(self, client):
        response = client.get("/liveness")
        assert response.status_code == 200


class TestUploadEndpoint:
    def test_upload_image_file(self, client, temp_dirs, monkeypatch):
        monkeypatch.setattr("settings.NUDE_FILTER_MAX_THRESHOLD", None)

        # Create a minimal valid PNG (1x1 transparent pixel)
        png_data = bytes([
            0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,  # PNG signature
            0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52,  # IHDR chunk
            0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,  # 1x1
            0x08, 0x06, 0x00, 0x00, 0x00, 0x1F, 0x15, 0xC4,  # 8-bit RGBA
            0x89, 0x00, 0x00, 0x00, 0x0A, 0x49, 0x44, 0x41,  # IDAT chunk
            0x54, 0x78, 0x9C, 0x63, 0x00, 0x01, 0x00, 0x00,
            0x05, 0x00, 0x01, 0x0D, 0x0A, 0x2D, 0xB4, 0x00,
            0x00, 0x00, 0x00, 0x49, 0x45, 0x4E, 0x44, 0xAE,  # IEND chunk
            0x42, 0x60, 0x82
        ])

        response = client.post(
            "/",
            files={"file": ("test.png", png_data, "image/png")}
        )
        assert response.status_code == 200
        data = response.json()
        assert "filename" in data
        assert data["filename"].endswith(".png")

        # Verify file was created
        assert os.path.exists(os.path.join(temp_dirs["images"], data["filename"]))

    def test_upload_without_file_returns_400(self, client):
        response = client.post("/")
        assert response.status_code == 400
        assert "File is missing" in response.json()["detail"]

    def test_upload_svg_file(self, client, temp_dirs, monkeypatch):
        monkeypatch.setattr("settings.NUDE_FILTER_MAX_THRESHOLD", None)

        svg_data = b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"></svg>'

        response = client.post(
            "/",
            files={"file": ("test.svg", svg_data, "image/svg+xml")}
        )
        assert response.status_code == 200
        data = response.json()
        assert "filename" in data
        assert data["filename"].endswith(".svg")


class TestGetImageEndpoint:
    def test_get_existing_image(self, client, temp_dirs):
        # Create a test file in the images directory
        test_filename = "test123.txt"
        test_content = b"test content"
        test_path = os.path.join(temp_dirs["images"], test_filename)
        with open(test_path, "wb") as f:
            f.write(test_content)

        response = client.get(f"/{test_filename}")
        assert response.status_code == 200
        assert response.content == test_content

    def test_get_nonexistent_image_returns_404(self, client, temp_dirs):
        response = client.get("/nonexistent.png")
        assert response.status_code == 404
