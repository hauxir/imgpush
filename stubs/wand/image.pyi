"""Type stubs for Wand Image class."""

from typing import Any

class Image:
    width: int
    height: int
    sequence: list[Any]

    def __init__(self, filename: str | None = None, **kwargs: Any) -> None: ...
    def __enter__(self) -> Image: ...
    def __exit__(self, *args: Any) -> None: ...
    def clone(self) -> Image: ...
    def convert(self, format: str) -> Image: ...
    def crop(
        self,
        left: int = 0,
        top: int = 0,
        right: int | None = None,
        bottom: int | None = None,
        width: int | None = None,
        height: int | None = None,
        reset_coords: bool = True,
        gravity: str | None = None,
    ) -> bool: ...
    def sample(self, width: int | None = None, height: int | None = None) -> bool: ...
    def save(
        self,
        file: Any | None = None,
        filename: str | None = None,
        adjoin: bool = True,
    ) -> None: ...
    def strip(self) -> None: ...
    def close(self) -> None: ...
