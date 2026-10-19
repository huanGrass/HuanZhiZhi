from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from .colors import AverageColor, ColorSimilarity
from .components import NmsRectList
from .rects import GameRect, _binary_array, _crop_array, _gray_array, _luma_array, _match_item, _rgb_array


def _find_template_array_list(
    search_array: np.ndarray,
    template_array: np.ndarray,
    roi_rect: GameRect,
    min_score: float,
    method: int,
    max_count: int | None,
    nms_threshold: float,
) -> list[dict[str, Any]]:
    template_height, template_width = template_array.shape[:2]
    if template_width <= 0 or template_height <= 0:
        return []
    if search_array.shape[1] < template_width or search_array.shape[0] < template_height:
        return []

    result = cv2.matchTemplate(search_array, template_array, method)
    result = np.nan_to_num(result, nan=0.0, posinf=0.0, neginf=0.0)
    y_list, x_list = np.where(result >= float(min_score))
    candidates = []
    for x, y in zip(x_list, y_list):
        score = float(result[y, x])
        candidates.append(_match_item(roi_rect.left + int(x), roi_rect.top + int(y), template_width, template_height, score))
    candidates.sort(key=lambda item: item["score"], reverse=True)
    return NmsRectList(candidates, iou_threshold=nms_threshold, max_count=max_count)


def FindTemplate(
    image,
    template,
    roi: GameRect | tuple | list | None = None,
    min_score: float = 0.80,
    method: int = cv2.TM_CCOEFF_NORMED,
) -> dict[str, Any] | None:
    match_list = FindTemplateList(image, template, roi=roi, min_score=min_score, method=method, max_count=1)
    return match_list[0] if match_list else None


def FindTemplateList(
    image,
    template,
    roi: GameRect | tuple | list | None = None,
    min_score: float = 0.80,
    method: int = cv2.TM_CCOEFF_NORMED,
    max_count: int = 20,
    nms_threshold: float = 0.30,
) -> list[dict[str, Any]]:
    search_rgb, roi_rect = _crop_array(image, roi)
    template_gray = _gray_array(template)
    search_gray = cv2.cvtColor(search_rgb, cv2.COLOR_RGB2GRAY)
    return _find_template_array_list(search_gray, template_gray, roi_rect, min_score, method, max_count, nms_threshold)


def FindTemplateLuma(
    image,
    template,
    roi: GameRect | tuple | list | None = None,
    min_score: float = 0.80,
    method: int = cv2.TM_CCOEFF_NORMED,
) -> dict[str, Any] | None:
    match_list = FindTemplateLumaList(image, template, roi=roi, min_score=min_score, method=method, max_count=1)
    return match_list[0] if match_list else None


def FindTemplateLumaList(
    image,
    template,
    roi: GameRect | tuple | list | None = None,
    min_score: float = 0.80,
    method: int = cv2.TM_CCOEFF_NORMED,
    max_count: int = 20,
    nms_threshold: float = 0.30,
) -> list[dict[str, Any]]:
    search_rgb, roi_rect = _crop_array(image, roi)
    search_luma = cv2.cvtColor(search_rgb, cv2.COLOR_RGB2YUV)[:, :, 0]
    template_luma = _luma_array(template)
    return _find_template_array_list(search_luma, template_luma, roi_rect, min_score, method, max_count, nms_threshold)


def FindTemplateBinary(
    image,
    template,
    roi: GameRect | tuple | list | None = None,
    min_score: float = 0.80,
    threshold: int | None = None,
    invert: bool = False,
    method: int = cv2.TM_CCOEFF_NORMED,
) -> dict[str, Any] | None:
    match_list = FindTemplateBinaryList(
        image,
        template,
        roi=roi,
        min_score=min_score,
        threshold=threshold,
        invert=invert,
        method=method,
        max_count=1,
    )
    return match_list[0] if match_list else None


def FindTemplateBinaryList(
    image,
    template,
    roi: GameRect | tuple | list | None = None,
    min_score: float = 0.80,
    threshold: int | None = None,
    invert: bool = False,
    method: int = cv2.TM_CCOEFF_NORMED,
    max_count: int = 20,
    nms_threshold: float = 0.30,
) -> list[dict[str, Any]]:
    search_rgb, roi_rect = _crop_array(image, roi)
    search_binary = _binary_array(search_rgb, threshold=threshold, invert=invert)
    template_binary = _binary_array(template, threshold=threshold, invert=invert)
    return _find_template_array_list(search_binary, template_binary, roi_rect, min_score, method, max_count, nms_threshold)


def FindTemplateWithColorCheck(
    image,
    template,
    expected_color: tuple[int, int, int],
    roi: GameRect | tuple | list | None = None,
    min_score: float = 0.80,
    min_color_similarity: int = 221,
    match_mode: str = "gray",
    max_count: int = 20,
    nms_threshold: float = 0.30,
) -> dict[str, Any] | None:
    match_list = FindTemplateWithColorCheckList(
        image,
        template,
        expected_color,
        roi=roi,
        min_score=min_score,
        min_color_similarity=min_color_similarity,
        match_mode=match_mode,
        max_count=max_count,
        nms_threshold=nms_threshold,
    )
    return match_list[0] if match_list else None


def FindTemplateWithColorCheckList(
    image,
    template,
    expected_color: tuple[int, int, int],
    roi: GameRect | tuple | list | None = None,
    min_score: float = 0.80,
    min_color_similarity: int = 221,
    match_mode: str = "gray",
    max_count: int = 20,
    nms_threshold: float = 0.30,
) -> list[dict[str, Any]]:
    search_count = max(20, int(max_count) * 5) if max_count is not None else None
    mode = match_mode.lower()
    if mode == "luma":
        candidates = FindTemplateLumaList(image, template, roi=roi, min_score=min_score, max_count=search_count, nms_threshold=nms_threshold)
    elif mode == "binary":
        candidates = FindTemplateBinaryList(image, template, roi=roi, min_score=min_score, max_count=search_count, nms_threshold=nms_threshold)
    else:
        candidates = FindTemplateList(image, template, roi=roi, min_score=min_score, max_count=search_count, nms_threshold=nms_threshold)

    result: list[dict[str, Any]] = []
    for item in candidates:
        average = AverageColor(image, item["rect"])
        color_score = ColorSimilarity(average, expected_color)
        if color_score < int(min_color_similarity):
            continue
        checked = dict(item)
        checked["color"] = list(average)
        checked["color_score"] = int(color_score)
        result.append(checked)
        if max_count is not None and len(result) >= max_count:
            break
    return result


def FindTemplateByMask(
    image,
    template,
    mask,
    roi: GameRect | tuple | list | None = None,
    min_score: float = 0.80,
    max_count: int = 20,
    nms_threshold: float = 0.30,
) -> list[dict[str, Any]]:
    search_rgb, roi_rect = _crop_array(image, roi)
    template_rgb = _rgb_array(template)
    mask_array = _gray_array(mask)
    template_height, template_width = template_rgb.shape[:2]
    if search_rgb.shape[1] < template_width or search_rgb.shape[0] < template_height:
        return []
    if mask_array.shape[:2] != template_rgb.shape[:2]:
        mask_array = cv2.resize(mask_array, (template_width, template_height), interpolation=cv2.INTER_NEAREST)
    mask_array = np.where(mask_array > 0, 255, 0).astype(np.uint8)
    if not np.any(mask_array):
        return []

    result = cv2.matchTemplate(search_rgb, template_rgb, cv2.TM_CCORR_NORMED, mask=mask_array)
    result = np.nan_to_num(result, nan=0.0, posinf=0.0, neginf=0.0)
    y_list, x_list = np.where(result >= float(min_score))
    candidates = []
    for x, y in zip(x_list, y_list):
        score = float(result[y, x])
        candidates.append(_match_item(roi_rect.left + int(x), roi_rect.top + int(y), template_width, template_height, score))
    candidates.sort(key=lambda item: item["score"], reverse=True)
    return NmsRectList(candidates, iou_threshold=nms_threshold, max_count=max_count)
