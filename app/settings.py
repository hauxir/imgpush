import contextlib
import os
from typing import Optional

IMAGES_DIR: str = "/images/"
CACHE_DIR: str = "/cache/"
OUTPUT_TYPE: Optional[str] = None
MAX_UPLOADS_PER_DAY: int = 1000
MAX_UPLOADS_PER_HOUR: int = 100
MAX_UPLOADS_PER_MINUTE: int = 20
ALLOWED_ORIGINS: list[str] = ["*"]
NAME_STRATEGY: str = "randomstr"
MAX_TMP_FILE_AGE: int = 5 * 60
RESIZE_TIMEOUT: int = 5
NUDE_FILTER_MAX_THRESHOLD: Optional[float] = None
NUDE_FILTER_VIDEO_INTERVAL: float = 1.0
ALLOW_VIDEO: bool = False
MAX_VIDEO_DURATION: float = 60.0
HIDE_UPLOAD_FORM: bool = False

VALID_SIZES: list[int] = []

MAX_SIZE_MB: int = 16

for variable in [item for item in globals() if not item.startswith("__")]:
    NULL = "NULL"
    env_var = os.getenv(variable, NULL).strip()
    if env_var is not NULL:
        with contextlib.suppress(Exception):
            env_var = eval(env_var)
    globals()[variable] = env_var if env_var is not NULL else globals()[variable]
