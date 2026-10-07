"""Turns raw pose + bar tracks into repetitions, quality checks and metrics.

Everything here is measurement. No pass/fail decisions are made in this file;
those come from skill/criteria.yaml via evaluate.py.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.signal import find_peaks, savgol_filter

from .pose import KP, PoseResult

OBS_CONF = 0.5      # keypoint confidence at/above which a point is "observed"
MIN_CONF = 0.25     # below this a point is treated as missing and interpolated
SEGMENTS = ["shoulder", "hip", "knee", "ankle", "ear"]


def _f(x, nd=3):
    """JSON-safe float."""
    if x is None:
        return None
    x = float(x)
    return None if (math.isnan(x) or math.isinf(x)) else round(x, nd)


def choose_side(kps: np.ndarray) -> str:
    """Pick the side facing the camera: higher mean confidence on the leg/torso chain."""
    scores = {}
    for side in ("left", "right"):
        idx = [KP[f"{side}_{s}"] for s in ("shoulder", "hip", "knee", "ankle")]
        scores[side] = float(np.nanmean(kps[:, idx, 2])) if len(kps) else 0.0
    return max(scores, key=scores.get)


def build_series(pose: PoseResult, side: str):
    """Per-landmark smoothed x/y, raw confidence, and an 'estimated' flag."""
    n = len(pose.times)
    win = max(5, int(round(pose.proc_fps * 0.35)) | 1)  # odd window ~0.35 s
    out = {}
    names = {
        "shoulder": f"{side}_shoulder", "hip": f"{side}_hip", "knee": f"{side}_knee",
        "ankle": f"{side}_ankle", "ear": f"{side}_ear", "nose": "nose",
        "elbow": f"{side}_elbow", "wrist": f"{side}_wrist",
    }
    for key, kp_name in names.items():
        k = pose.kps[:, KP[kp_name], :] if n else np.zeros((0, 3))
        conf = np.nan_to_num(k[:, 2], nan=0.0)
        good = conf >= MIN_CONF
        xs, ys = k[:, 0].copy(), k[:, 1].copy()
        if good.sum() >= 2:
            t = np.arange(n)
            xs = np.interp(t, t[good], xs[good])
            ys = np.interp(t, t[good], ys[good])
            if n > win:
                xs = savgol_filter(xs, win, 2)
                ys = savgol_filter(ys, win, 2)
        else:
            xs[:] = np.nan
            ys[:] = np.nan
        out[key] = {"x": xs, "y": ys, "conf": conf, "estimated": conf < OBS_CONF}
    return out


def _dist(s, a, b):
    return np.hypot(s[a]["x"] - s[b]["x"], s[a]["y"] - s[b]["y"])


def detect_reps(s, proc_fps: float):
    hip_y = s["hip"]["y"]
    if np.all(np.isnan(hip_y)):
        return [], float("nan")
    thigh = float(np.nanmedian(_dist(s, "hip", "knee")))
    y = np.nan_to_num(hip_y, nan=np.nanmean(hip_y))
    peaks, _ = find_peaks(y, prominence=0.35 * thigh, distance=max(1, int(0.8 * proc_fps)))
    reps = []
    bounds = [0] + list(peaks) + [len(y) - 1]
    for k, p in enumerate(peaks):
        lo, hi = bounds[k], bounds[k + 2]
        start = lo + int(np.argmin(y[lo:p + 1]))
        end = p + int(np.argmin(y[p:hi + 1]))
        if y[p] - y[start] < 0.35 * thigh or y[p] - y[end] < 0.35 * thigh:
            continue
        reps.append({"start": int(start), "bottom": int(p), "end": int(end)})
    return reps, thigh


def back_angle(s, i):
    dy = s["hip"]["y"][i] - s["shoulder"]["y"][i]   # positive when shoulder above hip
    dx = abs(s["shoulder"]["x"][i] - s["hip"]["x"][i])
    return math.degrees(math.atan2(dy, dx))


def joint_angle(s, a, b, c, i):
    v1 = np.array([s[a]["x"][i] - s[b]["x"][i], s[a]["y"][i] - s[b]["y"][i]])
    v2 = np.array([s[c]["x"][i] - s[b]["x"][i], s[c]["y"][i] - s[b]["y"][i]])
    d = np.linalg.norm(v1) * np.linalg.norm(v2)
    if d == 0 or np.isnan(d):
        return float("nan")
    return math.degrees(math.acos(np.clip(v1 @ v2 / d, -1, 1)))


def quality_checks(pose: PoseResult, s, side):
    n = len(pose.times)
    found = ~np.isnan(pose.boxes[:, 0]) if n else np.zeros(0, bool)
    found_ratio = float(found.mean()) if n else 0.0
    ls, rs = pose.kps[:, KP["left_shoulder"], 0], pose.kps[:, KP["right_shoulder"], 0]
    lh, rh = pose.kps[:, KP["left_hip"], 0], pose.kps[:, KP["right_hip"], 0]
    torso = _dist(s, "shoulder", "hip")
    sep = (np.abs(ls - rs) + np.abs(lh - rh)) / 2 / torso
    side_ratio = float(np.nanmedian(sep)) if n else float("nan")
    if np.isnan(side_ratio):
        view = "unknown"
    elif side_ratio < 0.35:
        view = "side"
    elif side_ratio < 0.6:
        view = "oblique"
    else:
        view = "front_or_back"
    # framing: are hip/knee/ankle inside the frame with margin?
    margin = 0.01
    inside = []
    for k in ("shoulder", "hip", "knee", "ankle"):
        x, y = s[k]["x"], s[k]["y"]
        inside.append((x > pose.width * margin) & (x < pose.width * (1 - margin)) &
                      (y > pose.height * margin) & (y < pose.height * (1 - margin)))
    in_frame = float(np.nanmean(np.all(inside, axis=0))) if n else 0.0
    mean_conf = {k: _f(np.nanmean(s[k]["conf"])) for k in ("shoulder", "hip", "knee", "ankle", "ear", "nose")}

    issues = []
    if found_ratio < 0.8:
        issues.append({"level": "error" if found_ratio < 0.4 else "warning", "code": "person_missing",
                       "message": f"A person was found in only {found_ratio:.0%} of frames."})
    if found_ratio < 0.4:
        issues.append({"level": "info", "code": "camera_level",
                       "message": "Nothing else can be checked without a clearly visible lifter."})
        return {"frames_processed": n, "person_found_ratio": _f(found_ratio), "view": "unknown",
                "side_separation_ratio": None, "in_frame_ratio": None, "facing_side": side,
                "mean_landmark_conf": mean_conf, "issues": issues}
    if view == "front_or_back":
        issues.append({"level": "error", "code": "not_side_view",
                       "message": "This looks like a front or back view. The standard's angles and depth landmarks need a side (profile) view, so body-position criteria are marked 'cannot assess'."})
    elif view == "oblique":
        issues.append({"level": "warning", "code": "oblique_view",
                       "message": "The camera is at an angle to the lifter rather than square to the side. Angles and distances are distorted; treat verdicts with extra caution."})
    if in_frame < 0.9:
        issues.append({"level": "warning", "code": "framing",
                       "message": "Parts of the body leave the frame during the video. Keep the whole lifter and the plates in view."})
    if pose.fps < 20:
        issues.append({"level": "warning", "code": "low_fps",
                       "message": f"Low frame rate ({pose.fps:.0f} fps); bottom position and timing are less precise."})
    issues.append({"level": "info", "code": "camera_level",
                   "message": "Assumes the camera is level (floor horizontal in the image). A tilted camera shifts every angle by the tilt."})
    return {
        "frames_processed": n, "person_found_ratio": _f(found_ratio), "view": view,
        "side_separation_ratio": _f(side_ratio), "in_frame_ratio": _f(in_frame),
        "facing_side": side, "mean_landmark_conf": mean_conf, "issues": issues,
    }


def compute(pose: PoseResult, markers: dict | None, bar_pos, bar_score):
    side = choose_side(pose.kps)
    s = build_series(pose, side)
    fps = pose.proc_fps
    reps, thigh = detect_reps(s, fps)
    quality = quality_checks(pose, s, side)
    n = len(pose.times)

    torso_len = float(np.nanmedian(_dist(s, "shoulder", "hip"))) if n else float("nan")
    shin = float(np.nanmedian(_dist(s, "knee", "ankle"))) if n else float("nan")

    # ---- markers (normalized 0-1 coordinates from the browser) ----
    W, H = pose.width, pose.height
    m = markers or {}
    heel = m.get("heel")
    toe = m.get("toe")
    foot = None
    if heel and toe:
        hx, hy, tx, ty = heel["x"] * W, heel["y"] * H, toe["x"] * W, toe["y"] * H
        fl = math.hypot(tx - hx, ty - hy)
        if fl > 5:
            mi = int(np.argmin(np.abs(np.array(pose.times) - float(m.get("t", 0)))))
            foot = {"heel": (hx, hy), "toe": (tx, ty), "mid": ((hx + tx) / 2, (hy + ty) / 2),
                    "length": fl, "fwd": 1.0 if tx > hx else -1.0, "frame": mi,
                    "ankle_at_mark": (s["ankle"]["x"][mi], s["ankle"]["y"][mi])}
    has_bar = bar_pos is not None and np.any(~np.isnan(bar_pos[:, 0]))

    def fwd_sign(rep):
        if foot:
            return foot["fwd"]
        b = rep["bottom"]
        d = s["knee"]["x"][b] - s["ankle"]["x"][b]
        return 1.0 if d >= 0 else -1.0

    rep_out = []
    for r_i, rep in enumerate(reps, start=1):
        a, b, e = rep["start"], rep["bottom"], rep["end"]
        fwd = fwd_sign(rep)
        conf_at_b = {k: _f(s[k]["conf"][b]) for k in ("shoulder", "hip", "knee", "ankle", "ear", "nose")}
        metrics, notes = {}, {}

        metrics["depth_margin"] = (s["hip"]["y"][b] - s["knee"]["y"][b]) / thigh

        metrics["back_angle_bottom"] = back_angle(s, b)

        # early ascent: first 30% of hip rise
        rise = s["hip"]["y"][b] - s["hip"]["y"][e]
        k = b
        for j in range(b, e + 1):
            if s["hip"]["y"][b] - s["hip"]["y"][j] >= 0.3 * rise:
                k = j
                break
        metrics["early_ascent_back_angle_change"] = back_angle(s, k) - back_angle(s, b) if k > b else float("nan")
        metrics["early_ascent_hip_forward"] = fwd * (s["hip"]["x"][k] - s["hip"]["x"][b]) / thigh if k > b else float("nan")
        early_frame = k

        # bottom dwell
        thr = s["hip"]["y"][b] - 0.03 * thigh
        lo = b
        while lo > a and s["hip"]["y"][lo - 1] >= thr:
            lo -= 1
        hi = b
        while hi < e and s["hip"]["y"][hi + 1] >= thr:
            hi += 1
        metrics["bottom_dwell_s"] = (hi - lo + 1) / fps

        # head pitch (ear -> nose) at bottom
        dx = fwd * (s["nose"]["x"][b] - s["ear"]["x"][b])
        dy = s["nose"]["y"][b] - s["ear"]["y"][b]
        metrics["head_pitch_down_bottom"] = math.degrees(math.atan2(dy, dx)) if dx == dx else float("nan")

        # ankle rise (proxy for heel lift)
        ank = s["ankle"]["y"][a:e + 1]
        base = np.nanmedian(np.concatenate([ank[: max(1, len(ank) // 6)], ank[-max(1, len(ank) // 6):]]))
        metrics["ankle_rise"] = (base - np.nanmin(ank)) / shin

        # foot-marker metrics
        foot_valid = False
        if foot:
            drift = math.hypot(s["ankle"]["x"][b] - foot["ankle_at_mark"][0],
                               s["ankle"]["y"][b] - foot["ankle_at_mark"][1]) / foot["length"]
            foot_valid = drift < 0.3
            if not foot_valid:
                notes["foot_markers"] = "The feet moved after you marked them, so the heel/toe marks no longer line up for this rep."
            else:
                metrics["knee_forward_of_toe"] = fwd * (s["knee"]["x"][b] - foot["toe"][0]) / foot["length"]
        else:
            notes["foot_markers"] = "Heel and toe were not marked."

        # bar metrics
        bar_ok_ratio = 0.0
        worst_bar_frame = None
        if has_bar:
            seg = bar_pos[a:e + 1]
            ok = ~np.isnan(seg[:, 0])
            bar_ok_ratio = float(ok.mean())
            if bar_ok_ratio < 0.8:
                notes["bar_marker"] = f"Bar tracking was lost in {1 - bar_ok_ratio:.0%} of this rep."
            else:
                bx = seg[ok, 0]
                if foot_valid:
                    off = np.abs(bx - foot["mid"][0]) / foot["length"]
                    metrics["bar_midfoot_max_offset"] = float(np.max(off))
                    worst_bar_frame = a + int(np.flatnonzero(ok)[int(np.argmax(off))])
                    metrics["bar_horizontal_drift"] = float((bx.max() - bx.min()) / foot["length"])
                if not np.isnan(bar_pos[b, 0]):
                    u = np.array([s["shoulder"]["x"][b] - s["hip"]["x"][b], s["shoulder"]["y"][b] - s["hip"]["y"][b]])
                    u = u / (np.linalg.norm(u) or 1)
                    v = np.array([bar_pos[b, 0] - s["shoulder"]["x"][b], bar_pos[b, 1] - s["shoulder"]["y"][b]])
                    metrics["bar_above_shoulder"] = float(v @ u / torso_len)
        else:
            notes["bar_marker"] = "The bar was not marked."

        observations = {
            "hip_angle_bottom_deg": _f(joint_angle(s, "shoulder", "hip", "knee", b), 1),
            "knee_angle_bottom_deg": _f(joint_angle(s, "hip", "knee", "ankle", b), 1),
            "back_angle_bottom_deg": _f(metrics["back_angle_bottom"], 1),
        }
        rep_out.append({
            "rep": r_i,
            "start_t": _f(pose.times[a]), "bottom_t": _f(pose.times[b]), "end_t": _f(pose.times[e]),
            "early_ascent_t": _f(pose.times[early_frame]),
            "worst_bar_t": _f(pose.times[worst_bar_frame]) if worst_bar_frame is not None else None,
            "frames": {"start": int(a), "bottom": int(b), "end": int(e), "early_ascent": int(early_frame)},
            "landmark_conf_at_bottom": conf_at_b,
            "bar_tracked_ratio": _f(bar_ok_ratio),
            "metrics": {k: _f(v, 4) for k, v in metrics.items()},
            "notes": notes,
            "observations": observations,
        })

    # ---- overlay track for the browser ----
    frames = []
    for i in range(n):
        f = {"t": _f(pose.times[i])}
        for k in ("shoulder", "hip", "knee", "ankle", "ear", "nose", "elbow", "wrist"):
            x, y = s[k]["x"][i], s[k]["y"][i]
            if not np.isnan(x):
                f[k] = [_f(x / W, 4), _f(y / H, 4), 1 if s[k]["conf"][i] >= OBS_CONF else 0]
        if has_bar and not np.isnan(bar_pos[i, 0]):
            f["bar"] = [_f(bar_pos[i, 0] / W, 4), _f(bar_pos[i, 1] / H, 4), _f(bar_score[i], 2)]
        frames.append(f)

    return {
        "video": {"width": W, "height": H, "fps": _f(pose.fps, 2), "proc_fps": _f(fps, 2),
                  "duration_s": _f(pose.times[-1] if n else 0)},
        "quality": quality,
        "scales": {"thigh_px": _f(thigh, 1), "shin_px": _f(shin, 1), "torso_px": _f(torso_len, 1),
                   "foot_px": _f(foot["length"], 1) if foot else None},
        "markers": {
            "bar": bool(has_bar),
            "foot": ({"heel": [foot["heel"][0] / W, foot["heel"][1] / H],
                      "toe": [foot["toe"][0] / W, foot["toe"][1] / H],
                      "mid": [foot["mid"][0] / W, foot["mid"][1] / H]} if foot else None),
        },
        "reps": rep_out,
        "frames": frames,
    }
