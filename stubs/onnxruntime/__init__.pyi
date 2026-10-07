from typing import Any, Optional, Sequence, Union

import numpy as np

class InferenceSession:
    def __init__(
        self,
        path_or_bytes: Union[str, bytes],
        sess_options: Optional[Any] = None,
        providers: Optional[Sequence[str]] = None,
    ) -> None: ...
    def run(
        self,
        output_names: Optional[list[str]],
        input_feed: dict[str, np.ndarray[Any, Any]],
        run_options: Optional[Any] = None,
    ) -> list[np.ndarray[Any, Any]]: ...
