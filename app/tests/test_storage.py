import os
import tempfile

import pytest


class TestLocalStorageBackend:
    @pytest.fixture
    def storage(self):
        from storage import LocalStorageBackend

        with tempfile.TemporaryDirectory() as tmp_dir:
            yield LocalStorageBackend(tmp_dir), tmp_dir

    def test_write_and_read_file(self, storage):
        backend, _ = storage
        backend.write_file("test.txt", b"hello world")
        assert backend.read_file("test.txt") == b"hello world"

    def test_file_exists(self, storage):
        backend, _ = storage
        assert not backend.file_exists("missing.txt")
        backend.write_file("exists.txt", b"data")
        assert backend.file_exists("exists.txt")

    def test_delete_file(self, storage):
        backend, _ = storage
        backend.write_file("to_delete.txt", b"data")
        assert backend.file_exists("to_delete.txt")
        backend.delete_file("to_delete.txt")
        assert not backend.file_exists("to_delete.txt")

    def test_upload_from_path(self, storage):
        backend, base_dir = storage
        # Create a temp file to upload
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
            f.write(b"uploaded content")
            src_path = f.name

        try:
            backend.upload_from_path(src_path, "uploaded.txt")
            assert backend.read_file("uploaded.txt") == b"uploaded content"
        finally:
            if os.path.exists(src_path):
                os.remove(src_path)

    def test_list_files(self, storage):
        backend, _ = storage
        backend.write_file("abc.png", b"1")
        backend.write_file("abc_100x100.png", b"2")
        backend.write_file("abc_200x200.png", b"3")
        backend.write_file("xyz.png", b"4")

        result = backend.list_files("abc")
        assert sorted(result) == ["abc.png", "abc_100x100.png", "abc_200x200.png"]

    def test_get_local_path(self, storage):
        backend, base_dir = storage
        backend.write_file("test.png", b"data")
        path = backend.get_local_path("test.png")
        assert path == os.path.join(base_dir, "test.png")
        assert os.path.isfile(path)
