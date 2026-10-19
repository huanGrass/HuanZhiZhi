from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from .rects import GameRect, _crop_array, _match_item


def _rects_from_mask(
    mask: np.ndarray,
    roi_rect: GameRect,
    min_area: int,
    max_area: int | None,
    nms_threshold: float,
    max_count: int | None,
) -> list[dict[str, Any]]:
    component_count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 4)
    result: list[dict[str, Any]] = []
    for index in range(1, component_count):
        area = int(stats[index, cv2.CC_STAT_AREA])
        if area < min_area:
            continue
        if max_area is not None and area > max_area:
            continue
        left = roi_rect.left + int(stats[index, cv2.CC_STAT_LEFT])
        top = roi_rect.top + int(stats[index, cv2.CC_STAT_TOP])
        width = int(stats[index, cv2.CC_STAT_WIDTH])
        height = int(stats[index, cv2.CC_STAT_HEIGHT])
        item = _match_item(left, top, width, height, 1.0)
        item["area"] = area
        result.append(item)
    result.sort(key=lambda item: item.get("area", 0), reverse=True)
    return NmsRectList(result, iou_threshold=nms_threshold, max_count=max_count)


def GetBBox(
    image,
    threshold: int = 0,
    roi: GameRect | tuple | list | None = None,
    invert: bool = False,
) -> list[int] | None:
    crop, roi_rect = _crop_array(image, roi)
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    if invert:
        mask = gray < int(threshold)
    else:
        mask = gray > int(threshold)
    if not np.any(mask):
        return None
    ys, xs = np.where(mask)
    return [
        roi_rect.left + int(xs.min()),
        roi_rect.top + int(ys.min()),
        roi_rect.left + int(xs.max()) + 1,
        roi_rect.top + int(ys.max()) + 1,
    ]


def GetMaskBBox(mask, threshold: int = 0, roi: GameRect | tuple | list | None = None) -> list[int] | None:
    return GetBBox(mask, threshold=threshold, roi=roi)


def _rect_from_item(item: dict[str, Any] | list | tuple) -> list[float]:
    rect = item.get("rect") if isinstance(item, dict) else item
    if rect is None or len(rect) != 4:
        return [0.0, 0.0, 0.0, 0.0]
    return [float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])]


def _rect_iou(first: dict[str, Any] | list | tuple, second: dict[str, Any] | list | tuple) -> float:
    left1, top1, right1, bottom1 = _rect_from_item(first)
    left2, top2, right2, bottom2 = _rect_from_item(second)
    inter_left = max(left1, left2)
    inter_top = max(top1, top2)
    inter_right = min(right1, right2)
    inter_bottom = min(bottom1, bottom2)
    inter_area = max(0.0, inter_right - inter_left) * max(0.0, inter_bottom - inter_top)
    first_area = max(0.0, right1 - left1) * max(0.0, bottom1 - top1)
    second_area = max(0.0, right2 - left2) * max(0.0, bottom2 - top2)
    union = first_area + second_area - inter_area
    if union <= 0:
        return 0.0
    return inter_area / union


def NmsRectList(
    candidates: list[dict[str, Any]],
    iou_threshold: float = 0.30,
    max_count: int | None = None,
) -> list[dict[str, Any]]:
    ordered = sorted(candidates, key=lambda item: float(item.get("score", item.get("area", 0))), reverse=True)
    result: list[dict[str, Any]] = []
    for item in ordered:
        if all(_rect_iou(item, existing) <= iou_threshold for existing in result):
            result.append(item)
            if max_count is not None and len(result) >= max_count:
                break
    return result
