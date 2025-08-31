"""Type stubs for timeout-decorator library."""

from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])

class TimeoutError(Exception): ...

def timeout(
    seconds: int | float | None = None,
    use_signals: bool = True,
    timeout_exception: type[Exception] = TimeoutError,
    exception_message: str | None = None,
) -> Callable[[F], F]: ...
