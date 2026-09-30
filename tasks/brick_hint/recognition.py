"""Locate the board and group matching artwork from the current frame only."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image


class RecognitionError(ValueError):
    pass

@dataclass(frozen=True)
class Grid:
    left: int
    top: int
    width: int
    height: int

    def center(self, index: int) -> tuple[int, int]:
        # Canonical frame coordinates; structural detection fits this frame
        # to the observed tile centers when a skin has different padding.
        return (round(self.left + (52.5 + index % 10 * 77.4) / 802 * self.width),
                round(self.top + (53.5 + index // 10 * 78.1) / 1127 * self.height))

    def tile(self, image: np.ndarray, index: int) -> np.ndarray:
        x, y = self.center(index)
        half_x, half_y = round(32 / 802 * self.width), round(32 / 1127 * self.height)
        return image[y-half_y:y+half_y, x-half_x:x+half_x]

def locate(image: Image.Image, previous_grid: Grid | None = None) -> Grid:
    choices = _structural_frames(image, previous_grid)
    if not choices:
        rgb = np.asarray(image.convert('RGB')).astype(np.int16)
        red, green, blue = rgb.transpose(2, 0, 1)
        mask = ((red > 145) & (green > 65) & (green < 195) & (blue < 110) & (red-green > 30)).astype(np.uint8)
        _, _, stats, _ = cv2.connectedComponentsWithStats(mask)
        choices = [(x,y,w,h) for x,y,w,h,area in stats[1:]
                   if w > image.width*.55 and h > image.height*.5 and 1.32 < h/w < 1.49 and area > w*h*.025]
    if len(choices) != 1:
        raise RecognitionError('未识别到唯一的14×10棋盘；请打开砖了个砖棋盘画面')
    return Grid(*(int(v) for v in choices[0]))


def _structural_frames(image: Image.Image, previous_grid: Grid | None) -> list[tuple[int, int, int, int]]:
    gray = cv2.cvtColor(np.asarray(image.convert('RGB')), cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 40, 100)
    # Close small breaks caused by border decoration and anti-aliasing.
    kernel = max(1, round(image.width/280))
    closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((kernel,kernel),np.uint8))
    outlines, _ = cv2.findContours(closed, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    # Closing can merge an intact frame with nearby artwork. Keep original
    # outlines too; the grid fit below deduplicates matching candidates.
    rectangles = [(cv2.boundingRect(c), cv2.contourArea(c)) for c in contours]
    outlines = rectangles + [(cv2.boundingRect(c), cv2.contourArea(c)) for c in outlines]
    tiles = [rect for rect,area in rectangles if area > rect[2]*rect[3]*.8]
    frames = []
    for (x,y,w,h),area in outlines:
        if not (w > image.width*.55 and h > image.height*.5 and 1.32 < h/w < 1.49):
            continue
        dx,dy = w*77.4/802, h*78.1/1127
        first_x,first_y = x+w*52.5/802, y+h*53.5/1127
        centers = []
        for tx,ty,tw,th in tiles:
            if not (.85*dx < tw < 1.05*dx and .85*dy < th < 1.05*dy):
                continue
            cx,cy = tx+tw/2, ty+th/2
            col,row = round((cx-first_x)/dx), round((cy-first_y)/dy)
            if (0 <= col < 10 and 0 <= row < 14
                    and abs(cx-first_x-col*dx) < .3*dx
                    and abs(cy-first_y-row*dy) < .3*dy):
                centers.append((col,row,cx,cy))
        cells = {(c,r) for c,r,_,_ in centers}
        closed_frame = area > w*h*.9
        # Broken contours need a well-populated lattice as independent
        # evidence; two isolated squares cannot establish a board.
        if not closed_frame and (len(cells) < 20 or len({c for c,r in cells}) < 3
                                 or len({r for c,r in cells}) < 3):
            continue
        if len(cells) < 2:
            # Once the last pair is gone, retain the measured geometry only
            # while a matching enclosing board outline is still visible.
            if previous_grid is not None:
                frame = (previous_grid.left, previous_grid.top, previous_grid.width, previous_grid.height)
                if max(abs(a-b) for a,b in zip(frame,(x,y,w,h))) < w*.04 and frame not in frames:
                    frames.append(frame)
            continue
        samples = np.asarray(centers)
        # Fit spacing and origin from the actual grid, not the outer skin's
        # snow caps, shadows or decorative margins.
        fitted = []
        for axis,pitch in ((0,dx),(1,dy)):
            indexes, coordinates = samples[:,axis], samples[:,axis+2]
            if len(set(indexes)) > 1:
                pitch,origin = np.polyfit(indexes, coordinates, 1)
            else:
                other = 1-axis
                if len(set(samples[:,other])) > 1:
                    other_pitch = np.polyfit(samples[:,other],samples[:,other+2],1)[0]
                    pitch = other_pitch * (77.4/78.1 if axis == 0 else 78.1/77.4)
                origin = np.median(coordinates-indexes*pitch)
            if np.max(np.abs(coordinates-(origin+indexes*pitch))) > pitch*.08:
                break
            fitted.append((pitch,origin))
        if len(fitted) != 2:
            continue
        (dx,first_x),(dy,first_y) = fitted
        width,height = dx*802/77.4, dy*1127/78.1
        frame = tuple(round(v) for v in (first_x-dx*52.5/77.4, first_y-dy*53.5/78.1, width,height))
        if frame[0] < 0 or frame[1] < 0 or frame[0]+frame[2] > image.width or frame[1]+frame[3] > image.height:
            continue
        if not any(max(abs(a-b) for a,b in zip(frame,other)) < min(dx,dy)*.1 for other in frames):
            frames.append(frame)
    return frames

def feature(tile: np.ndarray) -> np.ndarray:
    if tile.size == 0:
        raise RecognitionError('砖块范围越界')
    normalized = cv2.resize(tile, (32,32), interpolation=cv2.INTER_AREA).astype(np.float32)
    # Remove pale tile backgrounds so selection highlights do not dominate.
    pale = (normalized[:,:,0] > 180) & (normalized[:,:,1] > 175) & (normalized[:,:,2] > 95)
    normalized[pale] = 240
    return normalized / 255

def empty_cell(array: np.ndarray, grid: Grid, i: int) -> bool:
    crop = grid.tile(array, i)
    if not crop.size:
        raise RecognitionError(f'第{i//10+1}行第{i%10+1}列超出画面')
    # Empty cells have a flat interior continuing to the cell edges.
    # Estimate its color afresh: the board changes color during play.
    # Edge probes keep a solid obstruction over a tile's center from
    # being mistaken for an empty cell.
    background = np.median(crop, axis=(0,1))
    flat = np.mean(np.max(np.abs(crop.astype(int)-background), axis=2) < 25) > .92
    x, y = grid.center(i)
    dx, dy = round(34/802*grid.width), round(34/1127*grid.height)
    probes = ((x-dx,y),(x+dx,y),(x,y-dy),(x,y+dy))
    # A neighboring tile's shadow can cover one edge probe.
    continuous = sum(0 <= px < array.shape[1] and 0 <= py < array.shape[0]
                     and np.max(np.abs(array[py,px].astype(int)-background)) < 25
                     for px,py in probes) >= 3
    return bool(flat and continuous)

class Recognizer:
    def __init__(self):
        self._features = {}
        self._distances = np.empty((0, 0))
        self._grid = None

    def read(self, image: Image.Image, grid: Grid | None = None) -> tuple[bytes, Grid]:
        grid = grid or locate(image, self._grid)
        array = np.asarray(image.convert('RGB'))
        board = [0] * 140
        positions, features = [], []
        for i in range(140):
            crop = grid.tile(array, i)
            if not crop.size:
                raise RecognitionError(f'第{i//10+1}行第{i%10+1}列超出画面')
            if empty_cell(array, grid, i):
                continue
            positions.append(i)
            features.append(feature(crop))
        if not features:
            self._grid = grid
            return bytes(board), grid
        samples = np.stack(features)
        distances = np.full((len(samples), len(samples)), np.inf)
        # Reuse only exact normalized pixels; grouping and uncertainty checks
        # still run against the complete current board.
        current_indexes, previous_indexes = [], []
        for i, position in enumerate(positions):
            previous = self._features.get(position)
            if previous is not None and np.array_equal(samples[i], previous[1]):
                current_indexes.append(i)
                previous_indexes.append(previous[0])
        distances[np.ix_(current_indexes, current_indexes)] = self._distances[
            np.ix_(previous_indexes, previous_indexes)]
        for i, sample in enumerate(samples):
            for j in range(i):
                if np.isfinite(distances[i,j]):
                    continue
                # Window scaling rounds cell centers differently. Compare at
                # offsets up to two normalized pixels without wrapping edges.
                forward = cv2.matchTemplate(sample, samples[j,2:30,2:30], cv2.TM_SQDIFF).min()
                reverse = cv2.matchTemplate(samples[j], sample[2:30,2:30], cv2.TM_SQDIFF).min()
                distances[i,j] = distances[j,i] = max(0, min(forward,reverse)) / (28*28*3)
        # Group only close visual matches in this frame. No stored artwork or
        # named categories are used, and parity never forces a weak match.
        remaining = set(range(len(samples)))
        group_id = 0
        while remaining:
            group_id += 1
            members = {min(remaining)}
            pending = list(members)
            while pending:
                current = pending.pop()
                neighbors = set(np.flatnonzero(distances[current] < .03)) & remaining - members
                members.update(neighbors)
                pending.extend(neighbors)
            outside = set(range(len(samples))) - members
            if len(members) % 2:
                index = positions[min(members)]
                raise RecognitionError(f'第{index//10+1}行第{index%10+1}列无法可靠分组：相同图案数量为奇数')
            for member in members:
                same = distances[member, list(members-{member})]
                other = distances[member, list(outside)]
                if same.max() > .055 or (other.size and other.min()-same.min() < .008):
                    index = positions[member]
                    raise RecognitionError(f'第{index//10+1}行第{index%10+1}列图案分组不确定')
                board[positions[member]] = group_id
            remaining.difference_update(members)
        self._features = {position: (i, samples[i]) for i, position in enumerate(positions)}
        self._distances = distances
        self._grid = grid
        return bytes(board), grid
