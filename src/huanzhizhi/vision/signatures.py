from __future__ import annotations

from collections import Counter
from typing import Any

import cv2
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from .rects import _gray_array, _mask_array, _rgb_array


def BuildColorBlockSignature(
    image,
    valid_mask=None,
    foreground_mask=None,
    block_size: int = 3,
    color_quantize: int = 8,
    min_foreground_pixels: int = 3,
) -> dict[str, Any]:
    rgb = _rgb_array(image)
    height, width = rgb.shape[:2]
    block_size = max(1, int(block_size))
    color_quantize = max(1, int(color_quantize))
    min_foreground_pixels = max(0, int(min_foreground_pixels))
    if width < block_size or height < block_size:
        return {
            "blocks": Counter(),
            "block_count": 0,
            "foreground_pixels": 0,
            "block_size": block_size,
            "color_quantize": color_quantize,
            "min_foreground_pixels": min_foreground_pixels,
        }

    foreground = np.ones((height, width), dtype=bool)
    if foreground_mask is not None:
        foreground &= _mask_array(foreground_mask, (height, width)).astype(bool)

    quantized = (rgb // color_quantize).astype(np.uint8)
    color_blocks = sliding_window_view(quantized, (block_size, block_size), axis=(0, 1))
    foreground_blocks = sliding_window_view(foreground, (block_size, block_size)).sum(axis=(2, 3))
    selected_blocks = foreground_blocks >= min_foreground_pixels
    if valid_mask is not None:
        valid = _mask_array(valid_mask, (height, width)).astype(bool)
        valid_blocks = sliding_window_view(valid, (block_size, block_size)).all(axis=(2, 3))
        selected_blocks &= valid_blocks

    blocks: Counter = Counter()
    y_values, x_values = np.where(selected_blocks)
    for y, x in zip(y_values, x_values):
        blocks[color_blocks[y, x].tobytes()] += 1

    return {
        "blocks": blocks,
        "block_count": int(sum(blocks.values())),
        "foreground_pixels": int(np.count_nonzero(foreground)),
        "block_size": block_size,
        "color_quantize": color_quantize,
        "min_foreground_pixels": min_foreground_pixels,
    }


def CompareColorBlockSignatures(query_signature: dict[str, Any], template_signature: dict[str, Any]) -> dict[str, Any]:
    query_blocks = query_signature.get("blocks", Counter())
    template_blocks = template_signature.get("blocks", Counter())
    if not isinstance(query_blocks, Counter):
        query_blocks = Counter(query_blocks)
    if not isinstance(template_blocks, Counter):
        template_blocks = Counter(template_blocks)

    compared_blocks = int(sum(query_blocks.values()))
    template_blocks_count = int(sum(template_blocks.values()))
    matched_blocks = int(sum((query_blocks & template_blocks).values()))
    score = matched_blocks / float(compared_blocks) if compared_blocks else 0.0
    return {
        "score": float(score),
        "distance": float(1.0 - score),
        "matched_blocks": matched_blocks,
        "compared_blocks": compared_blocks,
        "template_blocks": template_blocks_count,
    }


def MatchColorBlockTemplateList(
    image,
    templates: list,
    labels: list[Any] | None = None,
    valid_mask=None,
    foreground_mask=None,
    block_size: int = 3,
    color_quantize: int = 8,
    min_foreground_pixels: int = 3,
    min_score: float = 0.0,
    max_count: int | None = 20,
) -> list[dict[str, Any]]:
    query_signature = BuildColorBlockSignature(
        image,
        valid_mask=valid_mask,
        foreground_mask=foreground_mask,
        block_size=block_size,
        color_quantize=color_quantize,
        min_foreground_pixels=min_foreground_pixels,
    )
    if query_signature["block_count"] <= 0:
        return []
    results: list[dict[str, Any]] = []
    for index, template in enumerate(templates):
        label = labels[index] if labels is not None and index < len(labels) else index
        signature = template if isinstance(template, dict) and "blocks" in template else BuildColorBlockSignature(
            template,
            block_size=block_size,
            color_quantize=color_quantize,
            min_foreground_pixels=min_foreground_pixels,
        )
        item = CompareColorBlockSignatures(query_signature, signature)
        if item["score"] < float(min_score):
            continue
        item["template_index"] = index
        item["label"] = label
        item["query_blocks"] = query_signature["block_count"]
        results.append(item)

    results.sort(key=lambda item: (float(item["score"]), int(item["matched_blocks"])), reverse=True)
    for index, item in enumerate(results):
        next_score = float(results[index + 1]["score"]) if index + 1 < len(results) else 0.0
        item["rank"] = index + 1
        item["margin"] = float(item["score"]) - next_score
    if max_count is not None:
        return results[: int(max_count)]
    return results


def ImageHash(image, method: str = "ahash", size: int = 8) -> tuple[int, ...]:
    gray = _gray_array(image)
    if method.lower() == "dhash":
        resized = cv2.resize(gray, (size + 1, size), interpolation=cv2.INTER_AREA)
        pixels = resized.astype(np.int16)
        return tuple(int(value) for value in (pixels[:, 1:] > pixels[:, :-1]).reshape(-1))
    resized = cv2.resize(gray, (size, size), interpolation=cv2.INTER_AREA)
    pixels = resized.astype(np.int16)
    average = float(pixels.mean())
    return tuple(int(value) for value in (pixels >= average).reshape(-1))


def HashDistance(first: tuple[int, ...], second: tuple[int, ...]) -> int:
    if len(first) != len(second):
        return max(len(first), len(second))
    return sum(1 for left, right in zip(first, second) if int(left) != int(right))
