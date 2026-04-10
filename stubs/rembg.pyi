from typing import Any, Optional, Union

from PIL import Image as PILImage

def remove(
    data: Union[bytes, PILImage.Image],
    alpha_matting: bool = False,
    alpha_matting_foreground_threshold: int = 240,
    alpha_matting_background_threshold: int = 10,
    alpha_matting_erode_size: int = 10,
    session: Optional[Any] = None,
    only_mask: bool = False,
    post_process_mask: bool = False,
    bgcolor: Optional[tuple[int, int, int, int]] = None,
    force_return_bytes: bool = False,
    *args: Optional[Any],
    **kwargs: Optional[Any],
) -> Union[bytes, PILImage.Image]: ...
