"""Burns the overlay into an MP4 (for download / sharing / submission evidence).

The in-app player draws the same overlay live on a canvas; this file mirrors
those conventions: solid dots = observed (conf >= 0.5), hollow = estimated.
"""
from __future__ import annotations

import json
import os
import subprocess

import cv2
import numpy as np

CHAIN = ["ear", "shoulder", "hip", "knee", "ankle"]
COL = {"body": (255, 220, 80), "bar": (60, 200, 255), "mid": (120, 255, 120),
       "meets": (90, 200, 90), "does_not_meet": (70, 70, 235), "cannot_assess": (170, 170, 170)}


def _interp(frames, t):
    times = [f["t"] for f in frames]
    i = int(np.searchsorted(times, t))
    if i <= 0:
        return frames[0]
    if i >= len(frames):
        return frames[-1]
    a, b = frames[i - 1], frames[i]
    w = (t - a["t"]) / max(1e-6, b["t"] - a["t"])
    out = {}
    for k in set(a) | set(b):
        if k == "t" or k not in a or k not in b:
            continue
        out[k] = [a[k][0] * (1 - w) + b[k][0] * w, a[k][1] * (1 - w) + b[k][1] * w, min(a[k][2], b[k][2])]
    return out


def render(job_dir: str, out_path: str):
    m = json.load(open(os.path.join(job_dir, "measurements.json")))
    a = json.load(open(os.path.join(job_dir, "assessment.json")))
    frames = m["frames"]
    findings = a["evaluation"]["findings"]
    cap = cv2.VideoCapture(os.path.join(job_dir, "video.mp4"))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    W, H = int(cap.get(3)), int(cap.get(4))
    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(fps),
         "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
         "-movflags", "+faststart", out_path], stdin=subprocess.PIPE)
    sc = max(1.0, H / 720)
    foot = (m.get("markers") or {}).get("foot")
    trail = []
    i = 0
    while True:
        ok, img = cap.read()
        if not ok:
            break
        t = i / fps
        f = _interp(frames, t) if frames else {}
        P = lambda k: (int(f[k][0] * W), int(f[k][1] * H))
        if foot:
            mx = int(foot["mid"][0] * W)
            cv2.line(img, (mx, 0), (mx, H), COL["mid"], max(1, int(1.5 * sc)), cv2.LINE_AA)
            for key in ("heel", "toe"):
                cv2.circle(img, (int(foot[key][0] * W), int(foot[key][1] * H)), int(5 * sc), COL["mid"], -1)
        pts = [k for k in CHAIN if k in f]
        for k1, k2 in zip(pts, pts[1:]):
            cv2.line(img, P(k1), P(k2), COL["body"], int(3 * sc), cv2.LINE_AA)
        for k in pts:
            cv2.circle(img, P(k), int(6 * sc), COL["body"], -1 if f[k][2] >= 1 else int(2 * sc), cv2.LINE_AA)
        if "bar" in f:
            trail.append(P("bar"))
            cv2.circle(img, P("bar"), int(9 * sc), COL["bar"], int(3 * sc), cv2.LINE_AA)
        trail = trail[-int(fps * 6):]
        for p1, p2 in zip(trail, trail[1:]):
            cv2.line(img, p1, p2, COL["bar"], max(1, int(2 * sc)), cv2.LINE_AA)
        # rep banner near each bottom
        for r in m["reps"]:
            if abs(t - r["bottom_t"]) < 0.6:
                fs = [x for x in findings if x["rep"] == r["rep"]]
                box_h = int((40 + 24 * len(fs)) * sc)
                roi = img[0:min(H, box_h), 0:min(W, int(330 * sc))]
                roi[:] = (roi * 0.35).astype(roi.dtype)
                y = int(30 * sc)
                cv2.putText(img, f"Rep {r['rep']} bottom", (int(12 * sc), y), cv2.FONT_HERSHEY_SIMPLEX, 0.8 * sc, (255, 255, 255), int(2 * sc), cv2.LINE_AA)
                for x in fs:
                    y += int(24 * sc)
                    mark = {"meets": "OK", "does_not_meet": "X", "cannot_assess": "?"}[x["result"]]
                    cv2.putText(img, f"{mark} {x['criterion_id']}", (int(12 * sc), y), cv2.FONT_HERSHEY_SIMPLEX, 0.55 * sc, COL[x["result"]], int(2 * sc), cv2.LINE_AA)
                if "knee" in f:
                    ky = P("knee")[1]
                    cv2.line(img, (0, ky), (W, ky), (255, 255, 255), 1, cv2.LINE_AA)
        proc.stdin.write(img.tobytes())
        i += 1
    cap.release()
    proc.stdin.close()
    proc.wait()
