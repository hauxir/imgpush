import glob
import os
import shutil
from abc import ABC, abstractmethod

import settings


class StorageBackend(ABC):
    @abstractmethod
    def file_exists(self, key: str) -> bool: ...

    @abstractmethod
    def read_file(self, key: str) -> bytes: ...

    @abstractmethod
    def write_file(self, key: str, data: bytes) -> None: ...

    @abstractmethod
    def upload_from_path(self, local_path: str, key: str) -> None: ...

    @abstractmethod
    def delete_file(self, key: str) -> None: ...

    @abstractmethod
    def list_files(self, prefix: str) -> list[str]: ...

    @abstractmethod
    def get_local_path(self, key: str) -> str: ...


class LocalStorageBackend(StorageBackend):
    def __init__(self, base_dir: str) -> None:
        self._base_dir = os.path.realpath(base_dir)

    def _full_path(self, key: str) -> str:
        path = os.path.realpath(os.path.join(self._base_dir, key))
        if not path.startswith(self._base_dir + os.sep) and path != self._base_dir:
            raise ValueError(f"Path traversal detected: {key}")
        return path

    def file_exists(self, key: str) -> bool:
        return os.path.isfile(self._full_path(key))

    def read_file(self, key: str) -> bytes:
        with open(self._full_path(key), "rb") as f:
            return f.read()

    def write_file(self, key: str, data: bytes) -> None:
        with open(self._full_path(key), "wb") as f:
            f.write(data)

    def upload_from_path(self, local_path: str, key: str) -> None:
        dest = self._full_path(key)
        if local_path != dest:
            shutil.move(local_path, dest)

    def delete_file(self, key: str) -> None:
        os.remove(self._full_path(key))

    def list_files(self, prefix: str) -> list[str]:
        pattern = os.path.join(self._base_dir, f"{glob.escape(prefix)}*")
        return [os.path.basename(p) for p in glob.glob(pattern)]

    def get_local_path(self, key: str) -> str:
        return self._full_path(key)


class S3StorageBackend(StorageBackend):
    def __init__(self, prefix: str) -> None:
        import boto3

        self._prefix = prefix
        self._bucket = settings.S3_BUCKET_NAME

        kwargs: dict[str, str] = {}
        if settings.S3_ENDPOINT_URL:
            kwargs["endpoint_url"] = settings.S3_ENDPOINT_URL
        if settings.S3_REGION:
            kwargs["region_name"] = settings.S3_REGION

        self._client = boto3.client(
            "s3",
            aws_access_key_id=settings.S3_ACCESS_KEY_ID,
            aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
            **kwargs,
        )

        os.makedirs(settings.LOCAL_CACHE_DIR, exist_ok=True)

    def _s3_key(self, key: str) -> str:
        return self._prefix + key

    def _cache_path(self, key: str) -> str:
        return os.path.join(settings.LOCAL_CACHE_DIR, self._prefix.replace("/", "_") + key)

    def file_exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=self._s3_key(key))
            return True
        except self._client.exceptions.ClientError:
            return False

    def read_file(self, key: str) -> bytes:
        response = self._client.get_object(Bucket=self._bucket, Key=self._s3_key(key))
        return response["Body"].read()

    def write_file(self, key: str, data: bytes) -> None:
        self._client.put_object(Bucket=self._bucket, Key=self._s3_key(key), Body=data)

    def upload_from_path(self, local_path: str, key: str) -> None:
        self._client.upload_file(local_path, self._bucket, self._s3_key(key))

    def delete_file(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=self._s3_key(key))
        # Also remove from local cache if present
        cache_path = self._cache_path(key)
        if os.path.isfile(cache_path):
            os.remove(cache_path)

    def list_files(self, prefix: str) -> list[str]:
        s3_prefix = self._s3_key(prefix)
        result: list[str] = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=s3_prefix):
            for obj in page.get("Contents", []):
                key: str = obj["Key"]
                result.append(key[len(self._prefix) :])
        return result

    def get_local_path(self, key: str) -> str:
        cache_path = self._cache_path(key)
        if os.path.isfile(cache_path):
            return cache_path

        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        self._client.download_file(self._bucket, self._s3_key(key), cache_path)
        self._evict_local_cache()
        return cache_path

    def _evict_local_cache(self) -> None:
        max_bytes = settings.LOCAL_CACHE_MAX_SIZE_MB * 1024 * 1024
        cache_dir = settings.LOCAL_CACHE_DIR

        files: list[tuple[str, float, int]] = []
        total_size = 0
        for entry in os.scandir(cache_dir):
            if entry.is_file():
                stat = entry.stat()
                files.append((entry.path, stat.st_atime, stat.st_size))
                total_size += stat.st_size

        if total_size <= max_bytes:
            return

        # Sort by access time ascending (oldest first)
        files.sort(key=lambda x: x[1])
        for path, _, size in files:
            if total_size <= max_bytes:
                break
            try:
                os.remove(path)
                total_size -= size
            except OSError:
                pass


_image_storage: StorageBackend | None = None
_cache_storage: StorageBackend | None = None


def get_image_storage() -> StorageBackend:
    global _image_storage
    if _image_storage is None:
        if settings.STORAGE_BACKEND == "s3":
            _image_storage = S3StorageBackend(prefix=settings.S3_IMAGES_PREFIX)
        else:
            _image_storage = LocalStorageBackend(settings.IMAGES_DIR)
    return _image_storage


def get_cache_storage() -> StorageBackend:
    global _cache_storage
    if _cache_storage is None:
        if settings.STORAGE_BACKEND == "s3":
            _cache_storage = S3StorageBackend(prefix=settings.S3_CACHE_PREFIX)
        else:
            _cache_storage = LocalStorageBackend(settings.CACHE_DIR)
    return _cache_storage
