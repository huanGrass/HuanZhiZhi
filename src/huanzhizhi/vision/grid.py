from __future__ import annotations

from typing import Any

import numpy as np


def GroupPoints(points: list[tuple[int, int]] | np.ndarray, threshold: int = 3) -> list[list[int]]:
    if points is None or len(points) == 0:
        return []
    remaining = np.asarray(points, dtype=np.float32)
    if remaining.ndim == 1:
        remaining = remaining.reshape(1, -1)
    groups: list[list[int]] = []
    while len(remaining):
        first = remaining[0]
        rest = remaining[1:]
        if len(rest):
            distance = np.sum(np.abs(rest - first), axis=1)
            grouped = np.vstack([rest[distance <= threshold], first])
            remaining = rest[distance > threshold]
        else:
            grouped = np.array([first])
            remaining = np.empty((0, 2), dtype=np.float32)
        groups.append([int(value) for value in np.rint(grouped.mean(axis=0)).astype(int)])
    return groups


def EstimateGridStep(values: list[int] | np.ndarray, min_step: int = 10, max_step: int | None = None, bin_size: int = 5) -> int | None:
    sorted_values = sorted(int(round(value)) for value in values)
    if len(sorted_values) < 2:
        return None
    bins: dict[int, int] = {}
    for index, value in enumerate(sorted_values):
        for other in sorted_values[index + 1 :]:
            diff = other - value
            if diff < min_step:
                continue
            if max_step is not None and diff > max_step:
                break
            key = int(round(diff / float(max(1, bin_size))) * max(1, bin_size))
            bins[key] = bins.get(key, 0) + 1
    if not bins:
        return None
    return max(bins.items(), key=lambda item: item[1])[0]


def BuildGridAxis(
    values: list[int] | np.ndarray,
    step: int,
    minimum: int | None = None,
    maximum: int | None = None,
    tolerance: int | None = None,
) -> list[int]:
    observed = sorted(int(round(value)) for value in values)
    if not observed or step <= 0:
        return []
    if minimum is None:
        minimum = observed[0]
    if maximum is None:
        maximum = observed[-1]
    if tolerance is None:
        tolerance = max(3, int(round(step * 0.13)))

    best_start = observed[0]
    best_score = -1
    for candidate in observed:
        score = 0
        for value in observed:
            distance = abs((value - candidate) % step)
            distance = min(distance, step - distance)
            if distance <= tolerance:
                score += 1
        if score > best_score:
            best_score = score
            best_start = candidate

    start = best_start
    while start - step >= minimum:
        start -= step
    axis = []
    value = start
    while value <= maximum:
        if value >= minimum:
            axis.append(int(round(value)))
        value += step
    return axis


def FindGridAxes(
    points: list[tuple[int, int]] | np.ndarray,
    x_bounds: tuple[int, int] | None = None,
    y_bounds: tuple[int, int] | None = None,
    min_step: int = 10,
    max_step: int | None = None,
    bin_size: int = 5,
) -> dict[str, Any]:
    grouped = GroupPoints(points, threshold=max(1, bin_size))
    if not grouped:
        return {"points": [], "x_step": None, "y_step": None, "xs": [], "ys": []}
    array = np.asarray(grouped, dtype=np.int32)
    x_step = EstimateGridStep(array[:, 0], min_step=min_step, max_step=max_step, bin_size=bin_size)
    y_step = EstimateGridStep(array[:, 1], min_step=min_step, max_step=max_step, bin_size=bin_size)
    xs = BuildGridAxis(array[:, 0], x_step, *(x_bounds or (None, None))) if x_step is not None else []
    ys = BuildGridAxis(array[:, 1], y_step, *(y_bounds or (None, None))) if y_step is not None else []
    return {"points": grouped, "x_step": x_step, "y_step": y_step, "xs": xs, "ys": ys}
