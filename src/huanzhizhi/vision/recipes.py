from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from .colors import (
    AverageColor,
    BuildColorMask,
    BuildSimilarColorMask,
    CountHsv,
    CountRgb,
    color_matches,
    sample_abs_rgb,
)
from .components import GetMaskBBox, NmsRectList, _rects_from_mask
from .grid import BuildGridAxis, EstimateGridStep, FindGridAxes
from .rects import GameRect, ToGameRect, _crop_array, _gray_array, _image_size
from .signatures import BuildColorBlockSignature, MatchColorBlockTemplateList


def FindMaskComponents(
    mask,
    offset: tuple[int, int] = (0, 0),
    min_area: int = 20,
    max_area: int | None = None,
    min_width: int | None = None,
    max_width: int | None = None,
    min_height: int | None = None,
    max_height: int | None = None,
    min_aspect: float | None = None,
    max_aspect: float | None = None,
    min_fill_ratio: float | None = None,
    max_fill_ratio: float | None = None,
    nms_threshold: float = 0.20,
    max_count: int | None = 50,
) -> list[dict[str, Any]]:
    width, height = _image_size(mask)
    left_offset, top_offset = int(offset[0]), int(offset[1])
    roi_rect = GameRect(left_offset, top_offset, left_offset + width, top_offset + height)
    binary = (_gray_array(mask) > 0).astype(np.uint8)
    candidates = _rects_from_mask(binary, roi_rect, min_area, max_area, nms_threshold=1.0, max_count=None)
    filtered = [
        item
        for item in (_enrich_rect_item(candidate) for candidate in candidates)
        if _rect_item_matches(
            item,
            min_width=min_width,
            max_width=max_width,
            min_height=min_height,
            max_height=max_height,
            min_aspect=min_aspect,
            max_aspect=max_aspect,
            min_fill_ratio=min_fill_ratio,
            max_fill_ratio=max_fill_ratio,
        )
    ]
    return NmsRectList(filtered, iou_threshold=nms_threshold, max_count=max_count)


def FindColorComponents(
    image,
    color: dict | None = None,
    hsv_color: dict | None = None,
    similar_color: tuple[int, int, int] | None = None,
    roi: GameRect | tuple | list | None = None,
    min_similarity: int = 221,
    invert: bool = False,
    min_area: int = 20,
    max_area: int | None = None,
    min_width: int | None = None,
    max_width: int | None = None,
    min_height: int | None = None,
    max_height: int | None = None,
    min_aspect: float | None = None,
    max_aspect: float | None = None,
    min_fill_ratio: float | None = None,
    max_fill_ratio: float | None = None,
    nms_threshold: float = 0.20,
    max_count: int | None = 50,
) -> list[dict[str, Any]]:
    mask = _build_combined_color_mask(
        image,
        roi=roi,
        color=color,
        hsv_color=hsv_color,
        similar_color=similar_color,
        min_similarity=min_similarity,
    )
    if invert:
        mask = np.where(mask > 0, 0, 255).astype(np.uint8)
    rect = ToGameRect(image, roi)
    return FindMaskComponents(
        mask,
        offset=(rect.left, rect.top),
        min_area=min_area,
        max_area=max_area,
        min_width=min_width,
        max_width=max_width,
        min_height=min_height,
        max_height=max_height,
        min_aspect=min_aspect,
        max_aspect=max_aspect,
        min_fill_ratio=min_fill_ratio,
        max_fill_ratio=max_fill_ratio,
        nms_threshold=nms_threshold,
        max_count=max_count,
    )


def BuildForegroundMask(
    image,
    roi: GameRect | tuple | list | None = None,
    background_color: tuple[int, int, int] | None = None,
    background_color_rule: dict | None = None,
    background_hsv_rule: dict | None = None,
    foreground_color_rule: dict | None = None,
    foreground_hsv_rule: dict | None = None,
    min_background_similarity: int = 221,
    invert: bool = False,
) -> np.ndarray:
    masks: list[np.ndarray] = []
    if background_color is not None:
        masks.append(BuildSimilarColorMask(image, background_color, roi=roi, min_similarity=min_background_similarity, invert=True) > 0)
    if background_color_rule is not None:
        masks.append(BuildColorMask(image, background_color_rule, roi=roi, invert=True) > 0)
    if background_hsv_rule is not None:
        masks.append(BuildColorMask(image, background_hsv_rule, roi=roi, hsv=True, invert=True) > 0)
    if foreground_color_rule is not None:
        masks.append(BuildColorMask(image, foreground_color_rule, roi=roi) > 0)
    if foreground_hsv_rule is not None:
        masks.append(BuildColorMask(image, foreground_hsv_rule, roi=roi, hsv=True) > 0)
    if not masks:
        raise ValueError("BuildForegroundMask needs a background or foreground rule")
    combined = masks[0].copy()
    for mask in masks[1:]:
        combined &= mask
    if invert:
        combined = ~combined
    return combined.astype(np.uint8) * 255


def FindForegroundComponents(
    image,
    roi: GameRect | tuple | list | None = None,
    background_color: tuple[int, int, int] | None = None,
    background_color_rule: dict | None = None,
    background_hsv_rule: dict | None = None,
    foreground_color_rule: dict | None = None,
    foreground_hsv_rule: dict | None = None,
    min_background_similarity: int = 221,
    min_area: int = 20,
    max_area: int | None = None,
    min_width: int | None = None,
    max_width: int | None = None,
    min_height: int | None = None,
    max_height: int | None = None,
    min_aspect: float | None = None,
    max_aspect: float | None = None,
    min_fill_ratio: float | None = None,
    max_fill_ratio: float | None = None,
    nms_threshold: float = 0.20,
    max_count: int | None = 50,
) -> list[dict[str, Any]]:
    mask = BuildForegroundMask(
        image,
        roi=roi,
        background_color=background_color,
        background_color_rule=background_color_rule,
        background_hsv_rule=background_hsv_rule,
        foreground_color_rule=foreground_color_rule,
        foreground_hsv_rule=foreground_hsv_rule,
        min_background_similarity=min_background_similarity,
    )
    rect = ToGameRect(image, roi)
    return FindMaskComponents(
        mask,
        offset=(rect.left, rect.top),
        min_area=min_area,
        max_area=max_area,
        min_width=min_width,
        max_width=max_width,
        min_height=min_height,
        max_height=max_height,
        min_aspect=min_aspect,
        max_aspect=max_aspect,
        min_fill_ratio=min_fill_ratio,
        max_fill_ratio=max_fill_ratio,
        nms_threshold=nms_threshold,
        max_count=max_count,
    )


def FindRegularGridCells(
    points: list[tuple[int, int]] | np.ndarray | None = None,
    rects: list[dict[str, Any]] | None = None,
    x_bounds: tuple[int, int] | None = None,
    y_bounds: tuple[int, int] | None = None,
    min_step: int = 10,
    max_step: int | None = None,
    bin_size: int = 5,
    tolerance: int | None = None,
    fill_missing: bool = False,
) -> dict[str, Any]:
    observed = _points_from_points_or_rects(points, rects)
    axes = FindGridAxes(observed, x_bounds=x_bounds, y_bounds=y_bounds, min_step=min_step, max_step=max_step, bin_size=bin_size)
    xs = axes["xs"]
    ys = axes["ys"]
    if not xs or not ys:
        return {**axes, "cells": [], "row_count": len(ys), "col_count": len(xs)}
    if tolerance is None:
        steps = [step for step in (axes["x_step"], axes["y_step"]) if step is not None]
        tolerance = max(3, int(round(min(steps) * 0.25))) if steps else max(1, bin_size)

    mapped: dict[tuple[int, int], dict[str, Any]] = {}
    for source_index, point in enumerate(observed):
        x, y = int(point[0]), int(point[1])
        col = _nearest_axis_index(xs, x, tolerance)
        row = _nearest_axis_index(ys, y, tolerance)
        if row is None or col is None:
            continue
        key = (row, col)
        distance = abs(xs[col] - x) + abs(ys[row] - y)
        existing = mapped.get(key)
        if existing is not None and int(existing["distance"]) <= distance:
            continue
        cell = {
            "row": row,
            "col": col,
            "x": xs[col],
            "y": ys[row],
            "center": [xs[col], ys[row]],
            "observed_center": [x, y],
            "occupied": True,
            "distance": distance,
            "source_index": source_index,
        }
        if rects is not None and source_index < len(rects):
            cell["source"] = rects[source_index]
        mapped[key] = cell

    cells: list[dict[str, Any]] = []
    if fill_missing:
        for row, y in enumerate(ys):
            for col, x in enumerate(xs):
                cells.append(mapped.get((row, col), {"row": row, "col": col, "x": x, "y": y, "center": [x, y], "occupied": False}))
    else:
        cells = [mapped[key] for key in sorted(mapped)]
    return {**axes, "cells": cells, "row_count": len(ys), "col_count": len(xs)}


def FindGridTileCells(
    image,
    centers: list[tuple[int, int]] | np.ndarray,
    roi: GameRect | tuple | list | None = None,
    step_hint: int | None = None,
    min_step: int = 10,
    max_step: int | None = None,
    bin_size: int = 5,
    min_final_step: int = 1,
    verify_color_rule: dict | None = None,
    verify_offset_ratio: float = 0.30,
    verify_min_matches: int = 4,
    fill_missing_threshold: float | None = 0.70,
    signature_foreground_rule: dict | None = None,
    signature_background_rule: dict | None = None,
    signature_half_ratio: float = 0.48,
    signature_bins: int = 512,
) -> dict[str, Any]:
    observed = [(int(point[0]), int(point[1])) for point in centers]
    roi_rect = ToGameRect(image, roi)
    if len(observed) < 2:
        return {"x_step": None, "y_step": None, "step": None, "xs": [], "ys": [], "cells": [], "row_count": 0, "col_count": 0}
    x_step = EstimateGridStep([x for x, _y in observed], min_step=min_step, max_step=max_step, bin_size=bin_size)
    y_step = EstimateGridStep([y for _x, y in observed], min_step=min_step, max_step=max_step, bin_size=bin_size)
    step = _choose_grid_tile_step(x_step, y_step, step_hint)
    if step is None or step < int(min_final_step):
        return {"x_step": x_step, "y_step": y_step, "step": None, "xs": [], "ys": [], "cells": [], "row_count": 0, "col_count": 0}

    xs = BuildGridAxis([x for x, _y in observed], step, roi_rect.left, roi_rect.right)
    ys = BuildGridAxis([y for _x, y in observed], step, roi_rect.top, roi_rect.bottom)
    tolerance = max(3, int(round(step * 0.25)))
    mapped: dict[tuple[int, int], dict[str, Any]] = {}
    for center_x, center_y in observed:
        col = _nearest_axis_index(xs, center_x, tolerance)
        row = _nearest_axis_index(ys, center_y, tolerance)
        if row is None or col is None:
            continue
        if verify_color_rule is not None and not HasGridTileAt(
            image,
            center_x,
            center_y,
            step,
            verify_color_rule,
            offset_ratio=verify_offset_ratio,
            min_matches=verify_min_matches,
        ):
            continue
        mapped[(row, col)] = _grid_tile_cell(
            image,
            row,
            col,
            center_x,
            center_y,
            step,
            signature_foreground_rule,
            signature_background_rule,
            signature_half_ratio,
            signature_bins,
        )

    total_slots = max(1, len(xs) * len(ys))
    if fill_missing_threshold is not None and len(mapped) / float(total_slots) >= float(fill_missing_threshold):
        for row, y in enumerate(ys):
            for col, x in enumerate(xs):
                if (row, col) in mapped:
                    continue
                if verify_color_rule is not None and not HasGridTileAt(
                    image,
                    x,
                    y,
                    step,
                    verify_color_rule,
                    offset_ratio=verify_offset_ratio,
                    min_matches=verify_min_matches,
                ):
                    continue
                mapped[(row, col)] = _grid_tile_cell(
                    image,
                    row,
                    col,
                    x,
                    y,
                    step,
                    signature_foreground_rule,
                    signature_background_rule,
                    signature_half_ratio,
                    signature_bins,
                )
    return {
        "x_step": x_step,
        "y_step": y_step,
        "step": step,
        "xs": xs,
        "ys": ys,
        "cells": [mapped[key] for key in sorted(mapped)],
        "row_count": len(ys),
        "col_count": len(xs),
    }


def HasGridTileAt(
    image,
    x: int,
    y: int,
    step: int,
    color_rule: dict,
    offset_ratio: float = 0.30,
    min_matches: int = 4,
    sample_radius: int = 2,
) -> bool:
    offset = max(1, int(round(int(step) * float(offset_ratio))))
    checks = (
        (x - offset, y - offset),
        (x + offset, y - offset),
        (x - offset, y + offset),
        (x + offset, y + offset),
        (x - offset, y),
        (x + offset, y),
        (x, y - offset),
        (x, y + offset),
    )
    matched = 0
    for px, py in checks:
        if color_matches(sample_abs_rgb(image, px, py, radius=sample_radius), color_rule):
            matched += 1
    return matched >= int(min_matches)


def BuildGridTileSignature(
    image,
    center_x: int,
    center_y: int,
    step: int,
    foreground_rule: dict | None = None,
    background_rule: dict | None = None,
    half_ratio: float = 0.48,
    bins: int = 512,
) -> tuple[int, ...]:
    half = max(1, int(round(int(step) * float(half_ratio))))
    width, height = _image_size(image)
    crop, _rect = _crop_array(
        image,
        (
            max(0, int(center_x) - half),
            max(0, int(center_y) - half),
            min(width, int(center_x) + half),
            min(height, int(center_y) + half),
        ),
    )
    if crop.size == 0:
        return tuple([0] * int(bins))
    pixels = crop.astype(np.int16)
    foreground = np.ones(pixels.shape[:2], dtype=bool)
    if foreground_rule is not None:
        foreground &= _rgb_rule_mask(pixels, foreground_rule)
    if background_rule is not None:
        foreground &= ~_rgb_rule_mask(pixels, background_rule)
    if not foreground.any():
        return tuple([0] * int(bins))
    color_bins = _quantized_color_bins(pixels, int(bins))
    histogram = np.bincount(color_bins[foreground].astype(np.int32), minlength=int(bins))
    histogram = np.rint(histogram * (1000.0 / float(foreground.sum()))).astype(np.int16)
    return tuple(int(value) for value in histogram)


def MatchPatchSignatureList(
    image,
    templates: list,
    labels: list[Any] | None = None,
    valid_mask=None,
    foreground_mask=None,
    background_color: tuple[int, int, int] | None = None,
    background_color_rule: dict | None = None,
    background_hsv_rule: dict | None = None,
    min_background_similarity: int = 221,
    block_size: int = 3,
    color_quantize: int = 8,
    min_foreground_pixels: int = 3,
    min_score: float = 0.0,
    max_count: int | None = 20,
) -> list[dict[str, Any]]:
    query_foreground = foreground_mask
    if query_foreground is None:
        query_foreground = _auto_foreground_mask(
            image,
            background_color=background_color,
            background_color_rule=background_color_rule,
            background_hsv_rule=background_hsv_rule,
            min_background_similarity=min_background_similarity,
        )
    template_signatures = []
    for template in templates:
        if isinstance(template, dict) and "blocks" in template:
            template_signatures.append(template)
            continue
        template_foreground = _auto_foreground_mask(
            template,
            background_color=background_color,
            background_color_rule=background_color_rule,
            background_hsv_rule=background_hsv_rule,
            min_background_similarity=min_background_similarity,
        )
        template_signatures.append(
            BuildColorBlockSignature(
                template,
                foreground_mask=template_foreground,
                block_size=block_size,
                color_quantize=color_quantize,
                min_foreground_pixels=min_foreground_pixels,
            )
        )
    return MatchColorBlockTemplateList(
        image,
        template_signatures,
        labels=labels,
        valid_mask=valid_mask,
        foreground_mask=query_foreground,
        block_size=block_size,
        color_quantize=color_quantize,
        min_foreground_pixels=min_foreground_pixels,
        min_score=min_score,
        max_count=max_count,
    )


def _choose_grid_tile_step(x_step: int | None, y_step: int | None, step_hint: int | None) -> int | None:
    candidates: list[int] = []
    if step_hint is not None:
        candidates.append(int(step_hint))
    for value in (x_step, y_step):
        if value is None:
            continue
        if step_hint is not None and not (step_hint * 0.70 <= value <= step_hint * 1.60):
            continue
        candidates.append(int(value))
    if not candidates:
        return None
    return int(round(sum(candidates) / float(len(candidates))))


def _grid_tile_cell(
    image,
    row: int,
    col: int,
    x: int,
    y: int,
    step: int,
    signature_foreground_rule: dict | None,
    signature_background_rule: dict | None,
    signature_half_ratio: float,
    signature_bins: int,
) -> dict[str, Any]:
    return {
        "row": row,
        "col": col,
        "x": x,
        "y": y,
        "center": [x, y],
        "size": step,
        "signature": BuildGridTileSignature(
            image,
            x,
            y,
            step,
            foreground_rule=signature_foreground_rule,
            background_rule=signature_background_rule,
            half_ratio=signature_half_ratio,
            bins=signature_bins,
        ),
    }


def _rgb_rule_mask(pixels: np.ndarray, rule: dict) -> np.ndarray:
    red = pixels[:, :, 0]
    green = pixels[:, :, 1]
    blue = pixels[:, :, 2]
    max_channel = pixels.max(axis=2)
    min_channel = pixels.min(axis=2)
    color_range = max_channel - min_channel
    mask = np.ones(red.shape, dtype=bool)
    if "red_min" in rule:
        mask &= red >= int(rule["red_min"])
    if "red_max" in rule:
        mask &= red <= int(rule["red_max"])
    if "green_min" in rule:
        mask &= green >= int(rule["green_min"])
    if "green_max" in rule:
        mask &= green <= int(rule["green_max"])
    if "blue_min" in rule:
        mask &= blue >= int(rule["blue_min"])
    if "blue_max" in rule:
        mask &= blue <= int(rule["blue_max"])
    if "max_channel_min" in rule:
        mask &= max_channel >= int(rule["max_channel_min"])
    if "range_min" in rule:
        mask &= color_range >= int(rule["range_min"])
    if "range_max" in rule:
        mask &= color_range <= int(rule["range_max"])
    return mask


def _quantized_color_bins(pixels: np.ndarray, bins: int) -> np.ndarray:
    if bins == 512:
        return (pixels[:, :, 0] // 32) * 64 + (pixels[:, :, 1] // 32) * 8 + (pixels[:, :, 2] // 32)
    levels = max(1, int(round(bins ** (1.0 / 3.0))))
    divisor = max(1, int(round(256 / float(levels))))
    return (pixels[:, :, 0] // divisor) * levels * levels + (pixels[:, :, 1] // divisor) * levels + (pixels[:, :, 2] // divisor)


def FindTextLikeBBox(
    image,
    roi: GameRect | tuple | list | None = None,
    mode: str = "dark",
    min_value: int | None = None,
    max_value: int | None = None,
    min_saturation: int | None = None,
    max_saturation: int | None = None,
    min_pixels: int = 1,
    min_width: int | None = None,
    min_height: int | None = None,
    padding: int = 0,
) -> dict[str, Any] | None:
    mask, roi_rect = _build_text_like_mask(
        image,
        roi=roi,
        mode=mode,
        min_value=min_value,
        max_value=max_value,
        min_saturation=min_saturation,
        max_saturation=max_saturation,
    )
    pixel_count = int(np.count_nonzero(mask))
    if pixel_count < int(min_pixels):
        return None
    bbox = GetMaskBBox(mask)
    if bbox is None:
        return None
    left, top, right, bottom = bbox
    pad = max(0, int(padding))
    left = max(0, left - pad)
    top = max(0, top - pad)
    right = min(mask.shape[1], right + pad)
    bottom = min(mask.shape[0], bottom + pad)
    width = right - left
    height = bottom - top
    if min_width is not None and width < int(min_width):
        return None
    if min_height is not None and height < int(min_height):
        return None
    global_rect = [roi_rect.left + left, roi_rect.top + top, roi_rect.left + right, roi_rect.top + bottom]
    return {
        "rect": global_rect,
        "bbox": global_rect,
        "pixels": pixel_count,
        "width": width,
        "height": height,
        "center": [global_rect[0] + width // 2, global_rect[1] + height // 2],
    }


def DiagnoseCandidates(
    image,
    candidates: list[dict[str, Any]] | None = None,
    points: list[tuple[str, float, float]] | None = None,
    color: dict | None = None,
    hsv_color: dict | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"candidates": [], "samples": []}
    for candidate in candidates or []:
        item = _enrich_rect_item(candidate)
        item["average_color"] = list(AverageColor(image, item["rect"]))
        if color is not None:
            item["rgb_mask_pixels"] = CountRgb(image, color, roi=item["rect"])
        if hsv_color is not None:
            item["hsv_mask_pixels"] = CountHsv(image, hsv_color, roi=item["rect"])
        result["candidates"].append(item)
    if points:
        from .colors import sample_rgb

        for name, x_ratio, y_ratio in points:
            pixel = sample_rgb(image, x_ratio, y_ratio)
            result["samples"].append({"name": name, "point": [x_ratio, y_ratio], "sample_rgb": list(pixel)})
    return result


def _build_combined_color_mask(
    image,
    roi: GameRect | tuple | list | None,
    color: dict | None,
    hsv_color: dict | None,
    similar_color: tuple[int, int, int] | None,
    min_similarity: int,
) -> np.ndarray:
    masks: list[np.ndarray] = []
    if color is not None:
        masks.append(BuildColorMask(image, color, roi=roi) > 0)
    if hsv_color is not None:
        masks.append(BuildColorMask(image, hsv_color, roi=roi, hsv=True) > 0)
    if similar_color is not None:
        masks.append(BuildSimilarColorMask(image, similar_color, roi=roi, min_similarity=min_similarity) > 0)
    if not masks:
        raise ValueError("FindColorComponents needs color, hsv_color, or similar_color")
    combined = masks[0].copy()
    for mask in masks[1:]:
        combined &= mask
    return combined.astype(np.uint8) * 255


def _enrich_rect_item(item: dict[str, Any]) -> dict[str, Any]:
    rect = list(item.get("rect", [0, 0, 0, 0]))
    left, top, right, bottom = (int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3]))
    width = max(0, right - left)
    height = max(0, bottom - top)
    area = int(item.get("area", width * height))
    enriched = dict(item)
    enriched["rect"] = [left, top, right, bottom]
    enriched["width"] = width
    enriched["height"] = height
    enriched["center"] = [left + width // 2, top + height // 2]
    enriched["aspect"] = width / float(height) if height else 0.0
    enriched["fill_ratio"] = area / float(max(1, width * height))
    return enriched


def _rect_item_matches(
    item: dict[str, Any],
    min_width: int | None,
    max_width: int | None,
    min_height: int | None,
    max_height: int | None,
    min_aspect: float | None,
    max_aspect: float | None,
    min_fill_ratio: float | None,
    max_fill_ratio: float | None,
) -> bool:
    width = int(item["width"])
    height = int(item["height"])
    aspect = float(item["aspect"])
    fill_ratio = float(item["fill_ratio"])
    if min_width is not None and width < int(min_width):
        return False
    if max_width is not None and width > int(max_width):
        return False
    if min_height is not None and height < int(min_height):
        return False
    if max_height is not None and height > int(max_height):
        return False
    if min_aspect is not None and aspect < float(min_aspect):
        return False
    if max_aspect is not None and aspect > float(max_aspect):
        return False
    if min_fill_ratio is not None and fill_ratio < float(min_fill_ratio):
        return False
    if max_fill_ratio is not None and fill_ratio > float(max_fill_ratio):
        return False
    return True


def _points_from_points_or_rects(points, rects: list[dict[str, Any]] | None) -> list[tuple[int, int]]:
    if points is not None:
        return [(int(point[0]), int(point[1])) for point in points]
    if rects is None:
        return []
    result = []
    for item in rects:
        if "center" in item:
            center = item["center"]
            result.append((int(center[0]), int(center[1])))
            continue
        rect = item.get("rect", [0, 0, 0, 0])
        result.append((int((rect[0] + rect[2]) / 2), int((rect[1] + rect[3]) / 2)))
    return result


def _nearest_axis_index(axis: list[int], value: int, tolerance: int) -> int | None:
    if not axis:
        return None
    index, axis_value = min(enumerate(axis), key=lambda item: abs(item[1] - value))
    return index if abs(axis_value - value) <= tolerance else None


def _auto_foreground_mask(
    image,
    background_color: tuple[int, int, int] | None,
    background_color_rule: dict | None,
    background_hsv_rule: dict | None,
    min_background_similarity: int,
) -> np.ndarray | None:
    if background_color is None and background_color_rule is None and background_hsv_rule is None:
        return None
    return BuildForegroundMask(
        image,
        background_color=background_color,
        background_color_rule=background_color_rule,
        background_hsv_rule=background_hsv_rule,
        min_background_similarity=min_background_similarity,
    )


def _build_text_like_mask(
    image,
    roi: GameRect | tuple | list | None,
    mode: str,
    min_value: int | None,
    max_value: int | None,
    min_saturation: int | None,
    max_saturation: int | None,
) -> tuple[np.ndarray, GameRect]:
    crop, roi_rect = _crop_array(image, roi)
    hsv = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV).astype(np.int16)
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]
    mask = np.ones(value.shape, dtype=bool)
    normalized_mode = mode.lower()
    if normalized_mode == "bright":
        min_value = 220 if min_value is None else min_value
    elif normalized_mode == "dark":
        max_value = 150 if max_value is None else max_value
    else:
        raise ValueError("mode must be 'dark' or 'bright'")
    if min_value is not None:
        mask &= value >= int(min_value)
    if max_value is not None:
        mask &= value <= int(max_value)
    if min_saturation is not None:
        mask &= saturation >= int(min_saturation)
    if max_saturation is not None:
        mask &= saturation <= int(max_saturation)
    return mask.astype(np.uint8) * 255, roi_rect
