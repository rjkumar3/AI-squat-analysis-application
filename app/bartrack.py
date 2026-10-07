"""Barbell tracking from a user-placed seed point (plate centre).

Normalized cross-correlation template matching in a local search window, run
forward and backward from the seed frame.

Drift protection (added after a test where the tracker latched onto a blank
wall once the tracked object disappeared):
  * every match is scored against the ORIGINAL seed template
  * low-texture candidates (wall, floor) are rejected
  * frames below LOST_SCORE are reported as lost, never guessed
  * after a loss, re-locking needs a stronger match (REACQUIRE_SCORE)
  * the adaptive template is only refreshed on confident matches
"""
from __future__ import annotations

import cv2
import numpy as np

LOST_SCORE = 0.6
REACQUIRE_SCORE = 0.7
UPDATE_SCORE = 0.75
MIN_TEXTURE = 0.35


def _decode(jpeg: bytes) -> np.ndarray:
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)
    return cv2.GaussianBlur(img, (3, 3), 0)


def _crop(img, cx, cy, half):
    h, w = img.shape
    x1, y1 = int(round(cx - half)), int(round(cy - half))
    x2, y2 = x1 + 2 * half, y1 + 2 * half
    if x1 < 0 or y1 < 0 or x2 > w or y2 > h:
        return None
    return img[y1:y2, x1:x2]


def _match(img, t0, t_last, cx, cy, half, radius, min_std):
    """Returns (score_vs_original, x, y) of the best acceptable candidate."""
    h, w = img.shape
    sx1, sy1 = max(0, int(cx - half - radius)), max(0, int(cy - half - radius))
    sx2, sy2 = min(w, int(cx + half + radius)), min(h, int(cy + half + radius))
    region = img[sy1:sy2, sx1:sx2]
    best = (-1.0, cx, cy)
    for t in (t0, t_last):
        if t is None or region.shape[0] < t.shape[0] or region.shape[1] < t.shape[1]:
            continue
        res = cv2.matchTemplate(region, t, cv2.TM_CCOEFF_NORMED)
        _, _, _, loc = cv2.minMaxLoc(res)
        x, y = sx1 + loc[0] + half, sy1 + loc[1] + half
        patch = _crop(img, x, y, half)
        if patch is None or patch.std() < min_std:
            continue
        s0 = float(cv2.matchTemplate(patch, t0, cv2.TM_CCOEFF_NORMED)[0, 0])
        if s0 > best[0]:
            best = (s0, x, y)
    return best


def track_bar(jpegs: list[bytes], seed_index: int, seed_xy: tuple[float, float],
              person_height_px: float, progress=lambda p: None):
    """Returns (positions (N,2) with NaN when lost, scores (N,), template half-size)."""
    n = len(jpegs)
    pos = np.full((n, 2), np.nan)
    score = np.zeros(n)
    half = int(np.clip(0.045 * person_height_px, 14, 70))
    radius = int(max(12, 0.08 * person_height_px))  # max expected motion between samples

    first = _decode(jpegs[seed_index])
    t0 = _crop(first, seed_xy[0], seed_xy[1], half)
    if t0 is None:
        half = max(8, half // 2)
        t0 = _crop(first, seed_xy[0], seed_xy[1], half)
    if t0 is None or t0.std() < 3:
        return pos, score, half  # seed on the frame edge or on a featureless area
    pos[seed_index] = seed_xy
    score[seed_index] = 1.0
    min_std = MIN_TEXTURE * float(t0.std())

    done = 1
    for direction in (1, -1):
        cx, cy = seed_xy
        t_last = t0
        lost_run = 0
        i = seed_index + direction
        while 0 <= i < n:
            img = _decode(jpegs[i])
            r = radius * (1 + min(lost_run, 6))
            s, x, y = _match(img, t0, t_last, cx, cy, half, r, min_std)
            need = LOST_SCORE if lost_run == 0 else REACQUIRE_SCORE
            if s >= need:
                pos[i] = (x, y)
                score[i] = s
                cx, cy = x, y
                lost_run = 0
                if s >= UPDATE_SCORE:
                    nt = _crop(img, x, y, half)
                    if nt is not None:
                        t_last = nt
            else:
                score[i] = max(s, 0.0)
                lost_run += 1
                t_last = t0
            i += direction
            done += 1
            progress(done / n)
    return pos, score, half
