from __future__ import annotations

import numpy as np

from .rects import _rgb_array


def ExtractLetters(image, letter: tuple[int, int, int] = (255, 255, 255), threshold: int = 128) -> np.ndarray:
    rgb = _rgb_array(image).astype(np.int16)
    target = np.asarray(letter[:3], dtype=np.int16)
    diff = rgb - target
    distance = np.maximum(diff, 0).max(axis=2) + np.maximum(-diff, 0).max(axis=2)
    if threshold != 255:
        distance = distance * (255.0 / max(1, int(threshold)))
    return np.clip(distance, 0, 255).astype(np.uint8)


def ExtractWhiteLetters(image, threshold: int = 128) -> np.ndarray:
    rgb = _rgb_array(image).astype(np.int16)
    distance_from_white = 255 - rgb
    maximum = distance_from_white.max(axis=2).astype(np.float32)
    minimum = distance_from_white.min(axis=2).astype(np.float32)
    result = maximum - minimum * 0.5
    if threshold != 255:
        result = result * (255.0 / max(1, int(threshold)))
    return np.clip(result, 0, 255).astype(np.uint8)
