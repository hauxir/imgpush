"""Type stubs for nudenet library."""

from typing import overload

class NudeClassifier:
    def __init__(self) -> None: ...
    @overload
    def classify(
        self,
        image_paths: str,
        *,
        batch_size: int = 4,
        image_size: tuple[int, int] = (256, 256),
        categories: list[str] = ...,
    ) -> dict[str, dict[str, float]]: ...
    @overload
    def classify(
        self,
        image_paths: list[str] = ...,
        *,
        batch_size: int = 4,
        image_size: tuple[int, int] = (256, 256),
        categories: list[str] = ...,
    ) -> dict[str, dict[str, float]]: ...
