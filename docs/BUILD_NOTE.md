# Build note

## What it does

Upload → validate → transcode → pose estimation → bar tracking → rep detection → measurement → rule evaluation (from the skill) → AI explanation (validated) → report with synchronized overlay.

```
browser (upload + marker taps, canvas overlay)
   │  POST /api/jobs  (XHR with upload progress)       GET /api/jobs/{id} (polled, stage + %)
   ▼
FastAPI, single background worker
   validate (ffprobe: readable, ≤60 s)  →  transcode (H.264, ≤1280 px, rotation applied)
   pose.py       YOLO11s-pose, 17 keypoints, sampled at 15 fps, single-lifter tracking by IoU
   bartrack.py   template tracking from the user's plate-centre tap, forward + backward
   analyze.py    side selection, smoothing, rep segmentation, quality gates, metrics   → measurements.json
   evaluate.py   applies skill/criteria.yaml (pure code, no LLM)                        → verdicts
   agent.py      Claude explains verdicts using SKILL.md; output validated             → assessment.json
```

## The key design decision

**Code measures, the skill decides, the model explains.**

- `criteria.yaml` holds every rule, band, page citation and feedback line. The app code only knows how to compute named metrics.
- The evaluator is deterministic. The LLM never sees a field it could use to change a result, and its output is merged *onto* the verdicts, not the other way round.
- Each tolerance is tagged `document` or `implementation_assumption`. The book says "about 45 degrees" and "directly over the midfoot" but gives no tolerances, so every band in this build is labelled as the app's assumption in the UI.
- The validator rejects AI text that cites an unknown criterion or uses a number not present in the report or skill. Rejected findings fall back to template text, and the rejection is shown on the report. Tested in `tests/test_core.py`.
- "Cannot assess" is produced by: wrong view for that measurement, missing marker, landmark confidence < 0.5 at the decisive frame, bar lost in > 20% of the rep, feet moved since being marked, or a value inside the borderline band.

## Criteria coverage

10 criteria are checked from side view: depth, bar over midfoot, vertical bar path, back angle at bottom, hip drive (two checks), knee position vs toes, feet flat, no pause at bottom, eye gaze, and low-bar position. The low-bar position can only ever fail, never pass, because the scapular spine isn't visible.

10 more standards are listed as **not assessable from a side view**, with pages: stance width, toe angle, knees out, back flatness, grip, elbows, rack height, unracking, breathing, programming. All are shown on every report.

## Model and tool choices

| Choice | Why | Trade-off |
|---|---|---|
| YOLO11s-pose (Ultralytics) on CPU | Per-keypoint confidence, robust to gym clutter, ~0.16 s/frame on 2 vCPU | COCO keypoints have **no heel or toe**, so midfoot needs user taps and "feet flat" uses ankle rise as a proxy. MediaPipe has foot points, but its model files were unreachable from my build environment (see below). |
| User taps plate/heel/toe once | Smallest reliable way to get bar and midfoot; feet don't move during a set | One manual step, disclosed in the UI. Automatic plate detection would need a trained detector and more time. |
| NCC template tracking | Plates have stable, high-contrast appearance in side view | Fails on heavy motion blur or plates leaving frame; reports "lost" rather than guessing. |
| Claude (Sonnet-class) for explanation only | Plain-language coaching that stays inside the skill | Adds a network call; app works without it. |
| FastAPI + vanilla JS, one container | No build step, easy to host | One worker processes videos serially. |

## Time and cost

- **Build time:** about 30 minutes of wall-clock agent time for this first version (22:14–22:45 on 6 Oct 2026), plus the earlier planning pass. Spent on skill extraction and page verification, pipeline, evaluator/validator, UI, testing and docs. *(Add your own time for running the common sample, deploying and reviewing.)*
- **Per video, compute:** about 2.4 s of CPU per second of video at 15 fps on 2 vCPU. A 30 s clip takes about 75 s end to end. On a small always-on container this is effectively fixed hosting cost.
- **Per video, AI:** about 10k input tokens (skill + report) and 2–4k output tokens, which is a few cents at Sonnet-class pricing (check current pricing). The re-check endpoint reuses measurements, so only the AI call repeats.
- **Build cost:** model API usage during development plus free-tier hosting.

## Verification done

| What | How | Result |
|---|---|---|
| Landmark placement | `scripts/evidence.py` stills of every bottom and early-ascent frame (`docs/evidence/bodyweight_oblique_bottom_frames.jpg`) | Hip, knee and ankle sit on the visible leg in all frames; hips visibly above knees, matching the "does not meet" depth verdict. |
| Bar tracker | Seeded on clasped hands (a bar stand-in) and compared with the pose model's wrist keypoints | Median 19 px from the wrist midpoint while tracked; correctly reports **lost** when the hands unclasp and re-acquires on the next rep. |
| View gating | 3/4-front bodyweight clip | Flagged "oblique"; angle and horizontal criteria become "cannot assess", vertical-only criteria (depth, pause, feet) still assessed. |
| Failure paths | Text file renamed .mp4, 70 s clip, empty grey clip, wrong extension | Clear messages: unreadable, too long, no reps found, unsupported type. |
| Rule change | Test edits the back-angle band in a copy of the skill | Verdict flips from meets to does not meet without code changes. |
| AI validation | Mock AI output with an invented "37.5 degrees", an unknown criterion and a non-failing priority | All three rejected and logged; the result can't be flipped. |
| UI | Headless Chromium screenshots at desktop and phone width (`docs/evidence/ui_*.png`) | Overlay aligned with the body in letterboxed video, seeking from scoreboard and rep buttons works. |

## Not yet verified (important)

- **The common sample (YouTube Short) has not been run.** YouTube, Pexels, Wikimedia and similar hosts were blocked from my build environment, and no side-view *barbell* squat video was reachable. The only real test footage was a bodyweight squat clip from the TensorFlow.js test data (3/4 view, 5 fps). Before submitting, download the common sample, run it through the app, and regenerate the evidence with `scripts/evidence.py`. Pay particular attention to bar tracking on real plates and the back-angle values.
- **The live AI path has not been exercised end to end** because there was no API key in the build environment. The validator and merge logic are unit-tested against mocked output; run one video with `ANTHROPIC_API_KEY` set and read the findings.
- **Thresholds are untuned.** The bands are reasoned, labelled assumptions, not calibrated against coach-labelled reps.

## Known limitations

- Depth is a **proxy**: hip-joint vs knee-joint keypoints instead of the book's hip crease vs top of kneecap. A borderline band absorbs this; close reps return "cannot assess".
- Back angle uses the shoulder–hip line, not the true plane of the back. Clothing and a loaded bar can shift the shoulder keypoint.
- Camera tilt isn't corrected; every angle shifts by the tilt. The UI states the assumption.
- Perspective: the plate is nearer the camera than the far foot, so bar-to-midfoot offsets carry a small parallax error.
- Multiple people: the lifter is chosen as the largest person and followed by box overlap; a spotter walking in front can steal the track.
- 15 fps sampling can miss the exact bottom by up to ~35 ms; fine for depth, coarse for the pause check.
- Jobs live in memory plus local disk; no auth. A single worker means queued uploads wait.

## How AI was used

**Delegated to the agent (Claude):** reading the reference and drafting the criteria with paraphrased citations; writing the pipeline, server, UI and tests; running the app headlessly and inspecting screenshots and frame stills.

**Inspected and decided by hand (and what a reviewer should check):**

- Every page citation was checked against per-page text extraction of the PDF (reference page = combined page − 3).
- Which numbers are document numbers versus assumptions.
- Angle conventions: image y points down, back angle is measured from horizontal, and "forward" comes from the heel→toe marks.
- The "never pass" rule for bar placement.

**Mistake caught and corrected: the bar tracker invented a bar path.** The first tracker accepted any match scoring ≥ 0.45 and kept updating its template. In the hands test it followed the hands correctly, then, once they disappeared, locked onto blank wall at scores of 0.5–0.6 and kept "tracking" it for the rest of the video. That would have produced a confident-looking bar path and bar-over-midfoot verdicts based on nothing. Before/after: `docs/evidence/bar_tracker_BEFORE_fix_drifts_to_wall.jpg` and `…AFTER_fix_reports_lost.jpg`. The fix:

- score every match against the original seed template, so drift can't hide;
- reject low-texture candidates;
- raise the lost threshold to 0.6, and require 0.7 to re-acquire after a loss;
- stop adapting the template while lost.

**Other corrections along the way:**

- MediaPipe's pose model couldn't be downloaded. Rebuilding its `.task` bundle from an older wheel failed on missing metadata, so the build switched to YOLO-pose and accepted the missing foot keypoints explicitly.
- Template-mode feedback duplicated the measurement text, which was removed.
- "The book gives no number" was inaccurate for depth (the book gives the rule, just no tolerance); the label was reworded.
