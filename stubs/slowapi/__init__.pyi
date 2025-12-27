from typing import Callable, List, Optional, TypeVar

F = TypeVar("F", bound=Callable[..., object])

class Limiter:
    def __init__(
        self,
        key_func: Callable[..., str],
        default_limits: Optional[List[str]] = None,
        application_limits: Optional[List[str]] = None,
        headers_enabled: bool = False,
        strategy: Optional[str] = None,
        storage_uri: Optional[str] = None,
        storage_options: Optional[dict[str, object]] = None,
        auto_check: bool = True,
        swallow_errors: bool = False,
        in_memory_fallback: Optional[List[str]] = None,
        in_memory_fallback_enabled: bool = False,
        retry_after: Optional[str] = None,
        key_prefix: str = "",
        enabled: bool = True,
    ) -> None: ...
    def limit(
        self,
        limit_value: str | Callable[..., str],
        key_func: Optional[Callable[..., str]] = None,
        per_method: bool = False,
        methods: Optional[List[str]] = None,
        error_message: Optional[str] = None,
        exempt_when: Optional[Callable[..., bool]] = None,
        cost: int | Callable[..., int] = 1,
        override_defaults: bool = True,
    ) -> Callable[[F], F]: ...
