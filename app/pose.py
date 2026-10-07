"""Pose extraction with YOLO11-pose (COCO 17 keypoints).

Decodes the (already transcoded) video once, runs the pose model on frames
sampled at PROCESS_FPS, follows a single lifter across frames, and keeps a
JPEG copy of every processed frame in memory for the bar tracker.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import cv2
import numpy as np

KP_NAMES = [
    "nose", "left_eye", "right_eye", "left_ear", "right_ear",
    "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
    "left_wrist", "right_wrist", "left_hip", "right_hip",
    "left_knee", "right_knee", "left_ankle", "right_ankle",
]
KP = {n: i for i, n in enumerate(KP_NAMES)}

MODEL_PATH = os.environ.get(
    "POSE_MODEL", os.path.join(os.path.dirname(__file__), "..", "models", "yolo11s-pose.pt")
)
PROCESS_FPS = float(os.environ.get("PROCESS_FPS", "15"))

_model = None


def get_model():
    global _model
    if _model is None:
        from ultralytics import YOLO

        _model = YOLO(MODEL_PATH)
    return _model


@dataclass
class PoseResult:
    fps: float                     # source fps
    proc_fps: float                # effective fps of the processed samples
    width: int
    height: int
    frame_idx: list[int] = field(default_factory=list)   # source frame index of each sample
    times: list[float] = field(default_factory=list)     # seconds
    kps: np.ndarray | None = None  # (N, 17, 3) x, y, conf in pixels; NaN where no person
    boxes: np.ndarray | None = None  # (N, 4) x1 y1 x2 y2
    jpegs: list[bytes] = field(default_factory=list)


def _iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def extract_pose(video_path: str, progress=lambda p: None) -> PoseResult:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError("Could not open the video file.")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    step = max(1, int(round(fps / PROCESS_FPS)))
    res = PoseResult(fps=fps, proc_fps=fps / step, width=w, height=h)
    model = get_model()

    kps_list, box_list = [], []
    prev_box = None
    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if i % step == 0:
            r = model(frame, verbose=False, imgsz=640)[0]
            chosen = None
            if r.boxes is not None and len(r.boxes) > 0:
                boxes = r.boxes.xyxy.cpu().numpy()
                kp = r.keypoints.data.cpu().numpy()  # (P, 17, 3)
                if prev_box is None:
                    areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
                    j = int(np.argmax(areas))
                else:
                    ious = [_iou(b, prev_box) for b in boxes]
                    j = int(np.argmax(ious))
                    if ious[j] < 0.1:  # lost the lifter; fall back to largest person
                        areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
                        j = int(np.argmax(areas))
                chosen = (boxes[j], kp[j])
                prev_box = boxes[j]
            if chosen is None:
                box_list.append([np.nan] * 4)
                kps_list.append(np.full((17, 3), np.nan))
            else:
                box_list.append(chosen[0])
                kps_list.append(chosen[1])
            res.frame_idx.append(i)
            res.times.append(i / fps)
            ok2, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            res.jpegs.append(buf.tobytes())
            if n:
                progress(min(0.99, i / n))
        i += 1
    cap.release()
    res.kps = np.array(kps_list, dtype=float) if kps_list else np.zeros((0, 17, 3))
    res.boxes = np.array(box_list, dtype=float) if box_list else np.zeros((0, 4))
    progress(1.0)
    return res
