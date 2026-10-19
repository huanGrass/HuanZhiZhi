from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from .components import _rects_from_mask
from .rects import GameRect, ToGameRect, _crop_array, _image_size, _rgb_array


def sample_rgb(image, x_ratio: float, y_ratio: float, radius: int = 2) -> tuple[int, int, int]:
    width, height = _image_size(image)
    center_x = max(0, min(width - 1, int(round(width * x_ratio))))
    center_y = max(0, min(height - 1, int(round(height * y_ratio))))
    return sample_abs_rgb(image, center_x, center_y, radius)


def sample_abs_rgb(image, x: int, y: int, radius: int = 2) -> tuple[int, int, int]:
    width, height = _image_size(image)
    center_x = max(0, min(width - 1, int(x)))
    center_y = max(0, min(height - 1, int(y)))
    left = max(0, center_x - radius)
    right = min(width - 1, center_x + radius)
    top = max(0, center_y - radius)
    bottom = min(height - 1, center_y + radius)
    rgb = _rgb_array(image)
    region = rgb[top : bottom + 1, left : right + 1].astype(np.uint32)
    if region.size == 0:
        return 0, 0, 0
    channel_sums = region.reshape(-1, 3).sum(axis=0)
    count = max(1, int(region.shape[0] * region.shape[1]))
    return int(channel_sums[0] // count), int(channel_sums[1] // count), int(channel_sums[2] // count)


def color_matches(pixel: tuple[int, int, int], config: dict) -> bool:
    red, green_value, blue_value = pixel
    max_channel = max(red, green_value, blue_value)
    min_channel = min(red, green_value, blue_value)
    color_range = max_channel - min_channel
    checks = (
        ("red_min", red >= config.get("red_min", 0)),
        ("red_max", red <= config.get("red_max", 255)),
        ("green_min", green_value >= config.get("green_min", 0)),
        ("green_max", green_value <= config.get("green_max", 255)),
        ("blue_min", blue_value >= config.get("blue_min", 0)),
        ("blue_max", blue_value <= config.get("blue_max", 255)),
        ("red_max_exclusive", red < config.get("red_max_exclusive", 256)),
        ("green_max_exclusive", green_value < config.get("green_max_exclusive", 256)),
        ("blue_max_exclusive", blue_value < config.get("blue_max_exclusive", 256)),
        ("max_channel_min", max_channel >= config.get("max_channel_min", 0)),
        ("max_channel_max", max_channel <= config.get("max_channel_max", 255)),
        ("min_channel_min", min_channel >= config.get("min_channel_min", 0)),
        ("min_channel_max", min_channel <= config.get("min_channel_max", 255)),
        ("range_min", color_range >= config.get("range_min", 0)),
        ("range_max", color_range <= config.get("range_max", 255)),
        ("red_minus_green_min", red - green_value >= config.get("red_minus_green_min", -255)),
        ("blue_minus_red_min", blue_value - red >= config.get("blue_minus_red_min", -255)),
        ("blue_minus_green_min", blue_value - green_value >= config.get("blue_minus_green_min", -255)),
        ("green_plus_margin_ge_red", green_value + config.get("green_plus_margin_ge_red", 255) >= red),
    )
    return all(result for key, result in checks if key in config)


def IsColor(img, color: dict, x: float, y: float) -> bool:
    return color_matches(sample_rgb(img, x, y), color)


def AverageColor(image, roi: GameRect | tuple | list | None = None) -> tuple[int, int, int]:
    crop, _rect = _crop_array(image, roi)
    if crop.size == 0:
        return 0, 0, 0
    red, green_value, blue, _alpha = cv2.mean(crop)
    return int(round(red)), int(round(green_value)), int(round(blue))


def ColorSimilarity(first: tuple[int, int, int], second: tuple[int, int, int]) -> int:
    diff = np.asarray(first[:3], dtype=np.int16) - np.asarray(second[:3], dtype=np.int16)
    positive = int(np.maximum(diff, 0).max())
    negative = int(np.maximum(-diff, 0).max())
    return max(0, min(255, 255 - positive - negative))


def ColorsSimilar(first: tuple[int, int, int], second: tuple[int, int, int], min_similarity: int = 221) -> bool:
    return ColorSimilarity(first, second) >= int(min_similarity)


def _color_similarity_array(array: np.ndarray, color: tuple[int, int, int]) -> np.ndarray:
    pixels = array.astype(np.int16)
    target = np.asarray(color[:3], dtype=np.int16)
    diff = pixels - target
    positive = np.maximum(diff, 0).max(axis=2)
    negative = np.maximum(-diff, 0).max(axis=2)
    similarity = 255 - positive - negative
    return np.clip(similarity, 0, 255).astype(np.uint8)


def ColorSimilarityMask(
    image,
    color: tuple[int, int, int],
    roi: GameRect | tuple | list | None = None,
) -> np.ndarray:
    crop, _rect = _crop_array(image, roi)
    return _color_similarity_array(crop, color)


def BuildSimilarColorMask(
    image,
    color: tuple[int, int, int],
    roi: GameRect | tuple | list | None = None,
    min_similarity: int = 221,
    invert: bool = False,
) -> np.ndarray:
    mask = ColorSimilarityMask(image, color, roi=roi) >= int(min_similarity)
    if invert:
        mask = ~mask
    return mask.astype(np.uint8) * 255


def CountSimilarColor(
    image,
    color: tuple[int, int, int],
    roi: GameRect | tuple | list | None = None,
    min_similarity: int = 221,
) -> int:
    mask = BuildSimilarColorMask(image, color, roi=roi, min_similarity=min_similarity)
    return int(cv2.countNonZero(mask))


def FindSimilarColorRectList(
    image,
    color: tuple[int, int, int],
    roi: GameRect | tuple | list | None = None,
    min_similarity: int = 221,
    min_area: int = 20,
    max_area: int | None = None,
    nms_threshold: float = 0.20,
    max_count: int = 50,
) -> list[dict[str, Any]]:
    mask = BuildSimilarColorMask(image, color, roi=roi, min_similarity=min_similarity)
    roi_rect = ToGameRect(image, roi)
    return _rects_from_mask(mask, roi_rect, min_area, max_area, nms_threshold, max_count)


def _rgb_mask(array: np.ndarray, color: dict) -> np.ndarray:
    pixels = array.astype(np.int16)
    red = pixels[:, :, 0]
    green_value = pixels[:, :, 1]
    blue = pixels[:, :, 2]
    max_channel = pixels.max(axis=2)
    min_channel = pixels.min(axis=2)
    color_range = max_channel - min_channel
    mask = np.ones(red.shape, dtype=bool)
    if "red_min" in color:
        mask &= red >= int(color["red_min"])
    if "red_max" in color:
        mask &= red <= int(color["red_max"])
    if "green_min" in color:
        mask &= green_value >= int(color["green_min"])
    if "green_max" in color:
        mask &= green_value <= int(color["green_max"])
    if "blue_min" in color:
        mask &= blue >= int(color["blue_min"])
    if "blue_max" in color:
        mask &= blue <= int(color["blue_max"])
    if "red_max_exclusive" in color:
        mask &= red < int(color["red_max_exclusive"])
    if "green_max_exclusive" in color:
        mask &= green_value < int(color["green_max_exclusive"])
    if "blue_max_exclusive" in color:
        mask &= blue < int(color["blue_max_exclusive"])
    if "max_channel_min" in color:
        mask &= max_channel >= int(color["max_channel_min"])
    if "max_channel_max" in color:
        mask &= max_channel <= int(color["max_channel_max"])
    if "min_channel_min" in color:
        mask &= min_channel >= int(color["min_channel_min"])
    if "min_channel_max" in color:
        mask &= min_channel <= int(color["min_channel_max"])
    if "range_min" in color:
        mask &= color_range >= int(color["range_min"])
    if "range_max" in color:
        mask &= color_range <= int(color["range_max"])
    if "red_minus_green_min" in color:
        mask &= red - green_value >= int(color["red_minus_green_min"])
    if "blue_minus_red_min" in color:
        mask &= blue - red >= int(color["blue_minus_red_min"])
    if "blue_minus_green_min" in color:
        mask &= blue - green_value >= int(color["blue_minus_green_min"])
    if "green_plus_margin_ge_red" in color:
        mask &= green_value + int(color["green_plus_margin_ge_red"]) >= red
    return mask.astype(np.uint8)


def _hsv_mask(array: np.ndarray, color: dict) -> np.ndarray:
    hsv = cv2.cvtColor(array, cv2.COLOR_RGB2HSV).astype(np.int16)
    hue = hsv[:, :, 0]
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]
    mask = np.ones(hue.shape, dtype=bool)
    if "hue_min" in color and "hue_max" in color:
        hue_min = int(color["hue_min"])
        hue_max = int(color["hue_max"])
        if hue_min <= hue_max:
            mask &= (hue >= hue_min) & (hue <= hue_max)
        else:
            mask &= (hue >= hue_min) | (hue <= hue_max)
    elif "hue_min" in color:
        mask &= hue >= int(color["hue_min"])
    elif "hue_max" in color:
        mask &= hue <= int(color["hue_max"])
    if "sat_min" in color:
        mask &= saturation >= int(color["sat_min"])
    if "sat_max" in color:
        mask &= saturation <= int(color["sat_max"])
    if "val_min" in color:
        mask &= value >= int(color["val_min"])
    if "val_max" in color:
        mask &= value <= int(color["val_max"])
    return mask.astype(np.uint8)


def BuildColorMask(
    image,
    color: dict,
    roi: GameRect | tuple | list | None = None,
    hsv: bool = False,
    invert: bool = False,
) -> np.ndarray:
    crop, _rect = _crop_array(image, roi)
    mask = _hsv_mask(crop, color) if hsv else _rgb_mask(crop, color)
    if invert:
        mask = 1 - mask
    return mask.astype(np.uint8) * 255


def CountRgb(image, color: dict, roi: GameRect | tuple | list | None = None) -> int:
    return int(cv2.countNonZero(BuildColorMask(image, color, roi=roi)))


def CountHsv(image, color: dict, roi: GameRect | tuple | list | None = None) -> int:
    return int(cv2.countNonZero(BuildColorMask(image, color, roi=roi, hsv=True)))


def FindColorRectList(
    image,
    color: dict,
    roi: GameRect | tuple | list | None = None,
    hsv: bool = False,
    min_area: int = 20,
    max_area: int | None = None,
    nms_threshold: float = 0.20,
    max_count: int = 50,
) -> list[dict[str, Any]]:
    mask = BuildColorMask(image, color, roi=roi, hsv=hsv)
    roi_rect = ToGameRect(image, roi)
    return _rects_from_mask(mask, roi_rect, min_area, max_area, nms_threshold, max_count)
