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


@pytest.fixture
def reset_rate_limiter():
    """Reset the rate limiter storage between tests."""
    from app import _auth_limiter
    _auth_limiter.storage.reset()
    yield
    _auth_limiter.storage.reset()


@pytest.fixture
def enable_api_key(monkeypatch):
    """Enable API key authentication."""
    monkeypatch.setattr("settings.API_KEY", "test-key")
    monkeypatch.setattr("settings.REQUIRE_API_KEY_FOR_DELETE", True)
    return "test-key"


class TestDeleteEndpoint:
    def test_delete_disabled_without_api_key(self, client, temp_dirs, monkeypatch):
        monkeypatch.setattr("settings.API_KEY", None)

        response = client.delete("/test.png", headers={"Authorization": "Bearer any"})
        assert response.status_code == 403
        assert "disabled" in response.json()["detail"]

    def test_delete_disabled_when_not_required(self, client, temp_dirs, monkeypatch):
        monkeypatch.setattr("settings.API_KEY", "test-key")
        monkeypatch.setattr("settings.REQUIRE_API_KEY_FOR_DELETE", False)

        response = client.delete("/test.png", headers={"Authorization": "Bearer test-key"})
        assert response.status_code == 403
        assert "disabled" in response.json()["detail"]

    def test_delete_invalid_api_key(self, client, temp_dirs, enable_api_key):
        response = client.delete("/test.png", headers={"Authorization": "Bearer wrong-key"})
        assert response.status_code == 403
        assert "Invalid API key" in response.json()["detail"]

    def test_delete_missing_auth_header(self, client, temp_dirs, enable_api_key):
        response = client.delete("/test.png")
        assert response.status_code == 403
        assert "Authorization required" in response.json()["detail"]

    def test_delete_invalid_auth_header_format(self, client, temp_dirs, enable_api_key):
        response = client.delete("/test.png", headers={"Authorization": "Basic abc123"})
        assert response.status_code == 403
        assert "Authorization required" in response.json()["detail"]

    def test_delete_nonexistent_file(self, client, temp_dirs, enable_api_key):
        response = client.delete("/nonexistent.png", headers={"Authorization": "Bearer test-key"})
        assert response.status_code == 404

    def test_delete_path_traversal_blocked(self, client, temp_dirs, enable_api_key):
        # URL-encoded path traversal attempt (..%2F = ../)
        response = client.delete("/..%2F..%2F..%2Fetc/passwd", headers={"Authorization": "Bearer test-key"})
        assert response.status_code == 400
        assert "Invalid filename" in response.json()["detail"]

    def test_delete_existing_file(self, client, temp_dirs, enable_api_key):
        # Create a test file
        test_filename = "test123.png"
        test_path = os.path.join(temp_dirs["images"], test_filename)
        with open(test_path, "wb") as f:
            f.write(b"test content")

        assert os.path.exists(test_path)

        response = client.delete(f"/{test_filename}", headers={"Authorization": "Bearer test-key"})
        assert response.status_code == 200
        assert response.json()["status"] == "deleted"

        # Verify file was deleted
        assert not os.path.exists(test_path)

    def test_delete_removes_cached_files(self, client, temp_dirs, enable_api_key):
        # Create original file and cached versions
        test_filename = "test123.png"
        test_path = os.path.join(temp_dirs["images"], test_filename)
        with open(test_path, "wb") as f:
            f.write(b"original")

        # Create cached resized versions
        cached_files = [
            "test123_100x100.png",
            "test123_200x150.png",
            "test123_50x50.png",
        ]
        for cached_name in cached_files:
            cached_path = os.path.join(temp_dirs["cache"], cached_name)
            with open(cached_path, "wb") as f:
                f.write(b"cached")

        response = client.delete(f"/{test_filename}", headers={"Authorization": "Bearer test-key"})
        assert response.status_code == 200
        assert response.json()["cached_files_removed"] == "3"

        # Verify all files were deleted
        assert not os.path.exists(test_path)
        for cached_name in cached_files:
            assert not os.path.exists(os.path.join(temp_dirs["cache"], cached_name))

    def test_delete_rate_limits_failed_attempts(self, client, temp_dirs, monkeypatch, reset_rate_limiter):
        monkeypatch.setattr("settings.API_KEY", "correct-key")
        monkeypatch.setattr("settings.REQUIRE_API_KEY_FOR_DELETE", True)

        # Update the parsed rate limit to use new value
        from limits import parse as parse_limit

        import app as app_module
        app_module._failed_auth_limit = parse_limit("3/minute")

        # Make 3 failed attempts (should all return 403)
        for i in range(3):
            response = client.delete("/test.png", headers={"Authorization": "Bearer wrong-key"})
            assert response.status_code == 403, f"Attempt {i+1} should return 403"

        # 4th attempt should be rate limited
        response = client.delete("/test.png", headers={"Authorization": "Bearer wrong-key"})
        assert response.status_code == 429
        assert "Too many failed attempts" in response.json()["detail"]

    def test_delete_rate_limit_does_not_affect_valid_requests(self, client, temp_dirs, monkeypatch, reset_rate_limiter):
        monkeypatch.setattr("settings.API_KEY", "correct-key")
        monkeypatch.setattr("settings.REQUIRE_API_KEY_FOR_DELETE", True)

        # Update the parsed rate limit
        from limits import parse as parse_limit

        import app as app_module
        app_module._failed_auth_limit = parse_limit("2/minute")

        # Make 2 failed attempts
        for _ in range(2):
            response = client.delete("/test.png", headers={"Authorization": "Bearer wrong-key"})
            assert response.status_code == 403

        # Valid request should still work (creates file first)
        test_filename = "valid-delete.png"
        test_path = os.path.join(temp_dirs["images"], test_filename)
        with open(test_path, "wb") as f:
            f.write(b"content")

        response = client.delete(f"/{test_filename}", headers={"Authorization": "Bearer correct-key"})
        assert response.status_code == 200


class TestUploadAuth:
    def test_upload_works_without_auth_when_not_required(self, client, temp_dirs, monkeypatch):
        monkeypatch.setattr("settings.NUDE_FILTER_MAX_THRESHOLD", None)
        monkeypatch.setattr("settings.API_KEY", "test-key")
        monkeypatch.setattr("settings.REQUIRE_API_KEY_FOR_UPLOAD", False)

        png_data = bytes([
            0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,
            0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52,
            0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,
            0x08, 0x06, 0x00, 0x00, 0x00, 0x1F, 0x15, 0xC4,
            0x89, 0x00, 0x00, 0x00, 0x0A, 0x49, 0x44, 0x41,
            0x54, 0x78, 0x9C, 0x63, 0x00, 0x01, 0x00, 0x00,
            0x05, 0x00, 0x01, 0x0D, 0x0A, 0x2D, 0xB4, 0x00,
            0x00, 0x00, 0x00, 0x49, 0x45, 0x4E, 0x44, 0xAE,
            0x42, 0x60, 0x82
        ])

        response = client.post("/", files={"file": ("test.png", png_data, "image/png")})
        assert response.status_code == 200

    def test_upload_requires_auth_when_enabled(self, client, temp_dirs, monkeypatch):
        monkeypatch.setattr("settings.API_KEY", "test-key")
        monkeypatch.setattr("settings.REQUIRE_API_KEY_FOR_UPLOAD", True)

        png_data = bytes([0x89, 0x50, 0x4E, 0x47])  # minimal PNG header

        response = client.post("/", files={"file": ("test.png", png_data, "image/png")})
        assert response.status_code == 403
        assert "Authorization required" in response.json()["detail"]

    def test_upload_with_valid_auth(self, client, temp_dirs, monkeypatch):
        monkeypatch.setattr("settings.NUDE_FILTER_MAX_THRESHOLD", None)
        monkeypatch.setattr("settings.API_KEY", "test-key")
        monkeypatch.setattr("settings.REQUIRE_API_KEY_FOR_UPLOAD", True)

        png_data = bytes([
            0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,
            0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52,
            0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,
            0x08, 0x06, 0x00, 0x00, 0x00, 0x1F, 0x15, 0xC4,
            0x89, 0x00, 0x00, 0x00, 0x0A, 0x49, 0x44, 0x41,
            0x54, 0x78, 0x9C, 0x63, 0x00, 0x01, 0x00, 0x00,
            0x05, 0x00, 0x01, 0x0D, 0x0A, 0x2D, 0xB4, 0x00,
            0x00, 0x00, 0x00, 0x49, 0x45, 0x4E, 0x44, 0xAE,
            0x42, 0x60, 0x82
        ])

        response = client.post(
            "/",
            files={"file": ("test.png", png_data, "image/png")},
            headers={"Authorization": "Bearer test-key"}
        )
        assert response.status_code == 200

    def test_upload_with_invalid_auth(self, client, temp_dirs, monkeypatch):
        monkeypatch.setattr("settings.API_KEY", "test-key")
        monkeypatch.setattr("settings.REQUIRE_API_KEY_FOR_UPLOAD", True)

        png_data = bytes([0x89, 0x50, 0x4E, 0x47])

        response = client.post(
            "/",
            files={"file": ("test.png", png_data, "image/png")},
            headers={"Authorization": "Bearer wrong-key"}
        )
        assert response.status_code == 403
        assert "Invalid API key" in response.json()["detail"]
