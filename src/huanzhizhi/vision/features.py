from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from .rects import GameRect, _crop_array, _gray_array


def _feature_detector(prefer: str):
    prefer = prefer.lower()
    if prefer == "sift":
        return cv2.SIFT_create(), cv2.NORM_L2, "sift"
    if prefer == "orb":
        return cv2.ORB_create(nfeatures=800), cv2.NORM_HAMMING, "orb"
    raise ValueError('prefer must be sift or orb')


def FindFeature(
    image,
    template,
    roi: GameRect | tuple | list | None = None,
    min_match_count: int = 8,
    ratio: float = 0.75,
    prefer: str = "sift",
) -> dict[str, Any]:
    if not 4 <= min_match_count <= 1000 or not 0 < ratio < 1:
        raise ValueError('Invalid feature matching threshold')
    search_rgb, roi_rect = _crop_array(image, roi)
    search_gray = cv2.cvtColor(search_rgb, cv2.COLOR_RGB2GRAY)
    template_gray = _gray_array(template)

    detector, norm_type, method_name = _feature_detector(prefer)
    template_points, template_des = detector.detectAndCompute(template_gray, None)
    search_points, search_des = detector.detectAndCompute(search_gray, None)
    if template_des is None or search_des is None or len(template_points) < 2 or len(search_points) < 2:
        return {"matched": False, "method": method_name, "match_count": 0, "score": 0.0, "rect": None}

    matcher = cv2.BFMatcher(norm_type)
    pairs = matcher.knnMatch(template_des, search_des, k=2)
    good_matches = []
    for pair in pairs:
        if len(pair) < 2:
            continue
        first, second = pair
        if first.distance < float(ratio) * second.distance:
            good_matches.append(first)

    rect = None
    inliers = 0
    if len(good_matches) >= min_match_count:
        src = np.float32([template_points[item.queryIdx].pt for item in good_matches]).reshape(-1, 1, 2)
        dst = np.float32([search_points[item.trainIdx].pt for item in good_matches]).reshape(-1, 1, 2)
        matrix, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
        inliers = int(mask.sum()) if mask is not None else 0
        if matrix is not None and inliers >= min_match_count:
            height, width = template_gray.shape[:2]
            corners = np.float32([[0, 0], [width, 0], [width, height], [0, height]]).reshape(-1, 1, 2)
            transformed = cv2.perspectiveTransform(corners, matrix).reshape(-1, 2)
            if (np.isfinite(transformed).all()
                    and cv2.isContourConvex(transformed.astype(np.float32))
                    and abs(cv2.contourArea(transformed)) >= 16
                    and transformed.min() >= -1
                    and transformed[:, 0].max() <= roi_rect.width + 1
                    and transformed[:, 1].max() <= roi_rect.height + 1):
                left = roi_rect.left + max(0, int(np.floor(transformed[:, 0].min())))
                top = roi_rect.top + max(0, int(np.floor(transformed[:, 1].min())))
                right = roi_rect.left + min(roi_rect.width, int(np.ceil(transformed[:, 0].max())))
                bottom = roi_rect.top + min(roi_rect.height, int(np.ceil(transformed[:, 1].max())))
                rect = [left, top, right, bottom]

    score = len(good_matches) / float(max(1, len(template_points)))
    return {
        "matched": rect is not None,
        "method": method_name,
        "match_count": len(good_matches),
        "inliers": inliers,
        "score": score,
        "rect": rect,
    }
