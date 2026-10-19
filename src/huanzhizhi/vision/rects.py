from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np


@dataclass(frozen=True)
class GameRect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)

    def to_list(self) -> list[int]:
        return [self.left, self.top, self.right, self.bottom]

    def clamp(self, width: int, height: int) -> "GameRect":
        return GameRect(
            max(0, min(width, int(self.left))),
            max(0, min(height, int(self.top))),
            max(0, min(width, int(self.right))),
            max(0, min(height, int(self.bottom))),
        )


def _image_size(image) -> tuple[int, int]:
    if isinstance(image, np.ndarray):
        height, width = image.shape[:2]
        return int(width), int(height)
    return image.size


def RatioRect(image, left: float, top: float, right: float, bottom: float) -> GameRect:
    width, height = _image_size(image)
    return GameRect(
        int(round(width * left)),
        int(round(height * top)),
        int(round(width * right)),
        int(round(height * bottom)),
    ).clamp(width, height)


def PixelRect(image, left: int, top: int, right: int, bottom: int) -> GameRect:
    width, height = _image_size(image)
    return GameRect(int(left), int(top), int(right), int(bottom)).clamp(width, height)


def ToGameRect(image, rect: GameRect | tuple | list | None = None) -> GameRect:
    width, height = _image_size(image)
    if rect is None:
        return GameRect(0, 0, width, height)
    if isinstance(rect, GameRect):
        return rect.clamp(width, height)
    if len(rect) != 4:
        raise ValueError(f"region needs 4 values: {rect!r}")
    left, top, right, bottom = rect
    values = [float(left), float(top), float(right), float(bottom)]
    if all(0.0 <= value <= 1.0 for value in values):
        return RatioRect(image, values[0], values[1], values[2], values[3])
    return PixelRect(image, int(round(values[0])), int(round(values[1])), int(round(values[2])), int(round(values[3])))


def CropRect(image, rect: GameRect | tuple | list | None = None):
    game_rect = ToGameRect(image, rect)
    if isinstance(image, np.ndarray):
        rgb = _rgb_array(image)
        return rgb[game_rect.top : game_rect.bottom, game_rect.left : game_rect.right]
    return image.crop((game_rect.left, game_rect.top, game_rect.right, game_rect.bottom))


def _rgb_array(image) -> np.ndarray:
    if isinstance(image, np.ndarray):
        array = image
        if array.ndim == 2:
            return cv2.cvtColor(array.astype(np.uint8), cv2.COLOR_GRAY2RGB)
        if array.shape[2] == 4:
            array = array[:, :, :3]
        return array.astype(np.uint8)
    return np.asarray(image.convert("RGB"), dtype=np.uint8)


def _gray_array(image) -> np.ndarray:
    return cv2.cvtColor(_rgb_array(image), cv2.COLOR_RGB2GRAY)


def _luma_array(image) -> np.ndarray:
    return cv2.cvtColor(_rgb_array(image), cv2.COLOR_RGB2YUV)[:, :, 0]


def _binary_array(image, threshold: int | None = None, invert: bool = False) -> np.ndarray:
    gray = _gray_array(image)
    flag = cv2.THRESH_BINARY_INV if invert else cv2.THRESH_BINARY
    if threshold is None:
        _used_threshold, binary = cv2.threshold(gray, 0, 255, flag | cv2.THRESH_OTSU)
    else:
        _used_threshold, binary = cv2.threshold(gray, int(threshold), 255, flag)
    return binary


def _mask_array(mask, shape: tuple[int, int]) -> np.ndarray:
    mask_array = _gray_array(mask)
    height, width = shape
    if mask_array.shape[:2] != (height, width):
        mask_array = cv2.resize(mask_array, (width, height), interpolation=cv2.INTER_NEAREST)
    return (mask_array > 0).astype(np.uint8)


def _crop_array(image, rect: GameRect | tuple | list | None = None) -> tuple[np.ndarray, GameRect]:
    game_rect = ToGameRect(image, rect)
    if (not isinstance(image, np.ndarray) and game_rect.width and game_rect.height
            and (game_rect.width, game_rect.height) != _image_size(image)):
        return _rgb_array(image.crop(tuple(game_rect.to_list()))), game_rect
    rgb = _rgb_array(image)
    return rgb[game_rect.top : game_rect.bottom, game_rect.left : game_rect.right], game_rect


def _match_item(left: int, top: int, width: int, height: int, score: float) -> dict[str, Any]:
    return {
        "x": int(left),
        "y": int(top),
        "point": [int(left), int(top)],
        "rect": [int(left), int(top), int(left + width), int(top + height)],
        "score": float(score),
    }
