from __future__ import annotations

from typing import Any

from .colors import CountHsv, CountRgb, color_matches, sample_rgb
from .rects import GameRect, ToGameRect, _gray_array
from .templates import FindTemplate, FindTemplateByMask


def DiagnoseMatch(
    image,
    roi: GameRect | tuple | list | None = None,
    template=None,
    mask=None,
    color: dict | None = None,
    hsv_color: dict | None = None,
    points: list[tuple[str, float, float]] | None = None,
) -> dict[str, Any]:
    rect = ToGameRect(image, roi)
    diagnosis: dict[str, Any] = {"roi": rect.to_list(), "samples": []}
    if points:
        for name, x_ratio, y_ratio in points:
            pixel = sample_rgb(image, x_ratio, y_ratio)
            diagnosis["samples"].append({"name": name, "point": [x_ratio, y_ratio], "sample_rgb": list(pixel)})
    if template is not None:
        if mask is not None:
            matches = FindTemplateByMask(image, template, mask, roi=rect, min_score=0.0, max_count=1)
            diagnosis["template"] = matches[0] if matches else None
        else:
            diagnosis["template"] = FindTemplate(image, template, roi=rect, min_score=0.0)
    if color is not None:
        diagnosis["rgb_mask_pixels"] = CountRgb(image, color, roi=rect)
    if hsv_color is not None:
        diagnosis["hsv_mask_pixels"] = CountHsv(image, hsv_color, roi=rect)
    if mask is not None:
        diagnosis["mask_pixels"] = int((_gray_array(mask) > 0).sum())
    return diagnosis


def DiagnoseState(img, checks: list[tuple[str, dict, float, float, bool]]) -> dict:
    result = []
    for color_name, color, x, y, should_match in checks:
        pixel = sample_rgb(img, x, y)
        matched = color_matches(pixel, color)
        matched = matched if should_match else not matched
        result.append(
            {
                "type": "point" if should_match else "not_point",
                "color": color_name,
                "point": [x, y],
                "sample_rgb": list(pixel),
                "matched": matched,
            }
        )
    return {"matched": all(item["matched"] for item in result), "checks": result}
