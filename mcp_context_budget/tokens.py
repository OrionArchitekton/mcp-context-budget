from __future__ import annotations

import math

ESTIMATOR_MODE = "chars_div_4_v1"


def estimate_tokens(text: str) -> int:
    """Deterministic local token estimate used by v1 reports and locks."""
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))
