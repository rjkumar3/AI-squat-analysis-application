"""End-to-end orchestration used by both the web server and the CLI."""
from __future__ import annotations

import json
import os
import subprocess

import numpy as np

from . import analyze, bartrack, evaluate, pose

MAX_DURATION_S = float(os.environ.get("MAX_DURATION_S", "60"))


def probe(path: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,r_frame_rate,codec_name:format=duration", "-of", "json", path],
        capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise ValueError("This file is not a readable video.")
    d = json.loads(out.stdout)
    if not d.get("streams"):
        raise ValueError("No video stream found in the file.")
    return {"duration": float(d.get("format", {}).get("duration", 0) or 0), **d["streams"][0]}


def transcode(src: str, dst: str):
    """H.264 / yuv420p / max 720p long side, rotation applied. Browser-safe and analysis input."""
    vf = "scale='if(gt(iw,ih),min(1280,iw),-2)':'if(gt(iw,ih),-2,min(1280,ih))',format=yuv420p"
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", src, "-an", "-vf", vf, "-c:v", "libx264",
         "-preset", "veryfast", "-crf", "23", "-movflags", "+faststart", dst],
        capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise ValueError("Could not decode this video: " + r.stderr.strip()[-300:])


def run(video_path: str, markers: dict | None, stage=lambda name, p: None):
    stage("pose", 0.0)
    pr = pose.extract_pose(video_path, progress=lambda p: stage("pose", p))
    if len(pr.times) < 5:
        raise ValueError("The video is too short to analyse.")

    bar_pos = bar_score = None
    if markers and markers.get("bar"):
        stage("bar", 0.0)
        t = float(markers.get("t", 0))
        idx = int(np.argmin(np.abs(np.array(pr.times) - t)))
        bx, by = markers["bar"]["x"] * pr.width, markers["bar"]["y"] * pr.height
        hts = pr.boxes[:, 3] - pr.boxes[:, 1]
        person_h = float(np.nanmedian(hts)) if np.any(~np.isnan(hts)) else pr.height * 0.6
        bar_pos, bar_score, _ = bartrack.track_bar(pr.jpegs, idx, (bx, by), person_h,
                                                   progress=lambda p: stage("bar", p))
    stage("measure", 0.5)
    measurements = analyze.compute(pr, markers, bar_pos, bar_score)
    stage("measure", 1.0)
    return measurements


def assess(measurements: dict, use_agent: bool = True, stage=lambda name, p: None):
    from . import agent

    stage("assess", 0.2)
    ev = evaluate.evaluate(measurements)
    stage("assess", 0.5)
    coached = agent.coach(measurements, ev) if use_agent else agent.template_coach(measurements, ev)
    stage("assess", 1.0)
    return {"evaluation": ev, "coaching": coached}
