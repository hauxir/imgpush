import contextlib
import os
from typing import Any, Optional

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
NUDE_FILTER_MAX_FRAMES: int = 5
ALLOW_VIDEO: bool = False
MAX_VIDEO_DURATION: float = 60.0
HIDE_UPLOAD_FORM: bool = False
API_KEY: Optional[str] = None
REQUIRE_API_KEY_FOR_UPLOAD: bool = False
REQUIRE_API_KEY_FOR_DELETE: bool = True
MAX_API_KEY_ATTEMPTS_PER_MINUTE: int = 5
ALLOW_REMOVE_BG: bool = False

STORAGE_BACKEND: str = "local"
S3_ENDPOINT_URL: str = ""
S3_ACCESS_KEY_ID: str = ""
S3_SECRET_ACCESS_KEY: str = ""
S3_BUCKET_NAME: str = ""
S3_REGION: str = ""
S3_IMAGES_PREFIX: str = "images/"
S3_CACHE_PREFIX: str = "cache/"
LOCAL_CACHE_DIR: str = "/local_cache/"
LOCAL_CACHE_MAX_SIZE_MB: int = 2048

VALID_SIZES: list[int] = []

MAX_SIZE_MB: int = 16
ALLOW_URL_UPLOAD: bool = True
ALLOW_PRIVATE_URLS: bool = False
MAX_CONCURRENT_URL_FETCHES: int = 8
MAX_CONCURRENT_URL_FETCHES_PER_IP: int = 2
# Ports upload-by-URL may fetch from, e.g. "80,443,8080". None allows any.
URL_UPLOAD_PORTS: Optional[list[int]] = [80, 443]
# Read by entrypoint.sh to configure nginx; here only to know whether it is set.
TRUSTED_PROXIES: Optional[str] = None

_TRUE_STRINGS = ("true", "1", "yes", "on")
_FALSE_STRINGS = ("false", "0", "no", "off", "none", "")


def parse_env_value(default: Any, raw: str, name: str = "setting") -> Any:
    """Turn an environment variable into a setting value.

    Values are evaluated as Python literals where possible. Settings whose
    default is a bool accept the usual spellings (true/false, 1/0, yes/no,
    on/off, any case) so that e.g. ALLOW_VIDEO=false doesn't end up as the
    truthy string "false". Values that used to work keep working: None is
    false, and quotes around the value (a compose file's `- X="True"` passes
    them through literally) are ignored.
    """
    raw = raw.strip()
    if isinstance(default, bool):
        word = raw.lower()
        if len(word) >= 2 and word[0] == word[-1] and word[0] in "'\"":
            word = word[1:-1].strip()
        if word in _TRUE_STRINGS:
            return True
        if word in _FALSE_STRINGS:
            return False
        raise ValueError(f"{name} must be a boolean (true/false, 1/0, yes/no, on/off), got {raw!r}")
    value: Any = raw
    with contextlib.suppress(Exception):
        value = eval(raw)
    return value


def parse_ports(value: Any, name: str = "setting") -> Optional[list[int]]:
    """Check a port-list setting, as parse_env_value left it, and return it as a list.

    Only an explicit None means "any port". Anything that is not a port or a
    list of ports is an error, an empty value included: a setting that limits
    where we connect to must not open up, or fail on every request, because of
    a typo.
    """
    if value is None:
        return None
    items: Any = [value] if isinstance(value, int) else value
    if (
        isinstance(items, (list, tuple, set, frozenset))
        and items
        and all(isinstance(port, int) and not isinstance(port, bool) and 0 < port < 65536 for port in items)  # pyright: ignore[reportUnknownVariableType]
    ):
        return sorted(set(items))  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
    raise ValueError(
        f"{name} must be a comma-separated list of ports such as 80,443, or None to allow every port, got {value!r}"
    )


for variable in [item for item in globals() if not item.startswith("_") and item.isupper()]:
    env_var = os.getenv(variable)
    if env_var is not None:
        globals()[variable] = parse_env_value(globals()[variable], env_var, variable)

globals()["URL_UPLOAD_PORTS"] = parse_ports(URL_UPLOAD_PORTS, "URL_UPLOAD_PORTS")
