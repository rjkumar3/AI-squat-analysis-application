# Squat Standard Coach

Upload a side-view video of a barbell squat set. The app tracks hips, knees, ankles, shoulders and the bar, finds every repetition and its bottom position, and checks each rep against a coaching standard extracted from the supplied reference chapter ("The Squat"). Every verdict links to the frame it is based on and the page it comes from. When the video can't support a verdict, the result is **cannot assess**, with the reason.

- **Skill (the standard):** [`skill/criteria.yaml`](skill/criteria.yaml) + [`skill/SKILL.md`](skill/SKILL.md)
- **Build note, AI usage, limitations:** [`docs/BUILD_NOTE.md`](docs/BUILD_NOTE.md)
- **Verification evidence:** [`docs/evidence/`](docs/evidence)

## Run locally

Requirements: Python 3.11+, `ffmpeg` on PATH.

```bash
pip install -r requirements.txt
mkdir -p models
curl -L -o models/yolo11s-pose.pt https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11s-pose.pt
export ANTHROPIC_API_KEY=...        # optional, see below
uvicorn app.main:app --port 8000
# open http://localhost:8000
```

Or with Docker (CPU only, ~1.5 GB image):

```bash
docker build -t squat-coach .
docker run -p 8000:8000 -e ANTHROPIC_API_KEY=... squat-coach
```

Any container host works (Render, Fly.io, Railway, Hugging Face Spaces with the Docker SDK). Give it 2 vCPU / 2 GB RAM. Mount a volume at `/data` if you want reports to survive restarts.

## Environment variables

| Name | Required | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | No | Enables the AI coach that writes explanations. Without it, feedback text comes straight from the skill file and the UI says so. Verdicts are identical either way. |
| `CLAUDE_MODEL` | No | Default `claude-sonnet-5-5`. |
| `PROCESS_FPS` | No | Pose sampling rate, default 15. |
| `MAX_DURATION_S` / `MAX_UPLOAD_MB` | No | Defaults 60 s / 150 MB. |
| `POSE_MODEL`, `DATA_DIR`, `SKILL_DIR` | No | Paths. |

## Using it

1. Choose a video. Optionally scrub to a clear frame and mark **the centre of the plate**, **the back of the heel** and **the tip of the shoe**. These enable the bar-path and midfoot checks; skipping them makes those checks "cannot assess".
2. **Analyse video.** You'll see upload progress, then each processing stage.
3. Read the result: a headline, a rep × criterion scoreboard (click any square to jump to the evidence frame), findings with measured values and the band they were judged against, and the standards a side view can't show.
4. **Download annotated video** gives an MP4 with the overlay burned in.

## Changing a rule

Edit `skill/criteria.yaml` (for example, the back-angle band) and click **Re-check with current standard** on any report, or call `POST /api/jobs/{id}/reassess`. Measurements are cached, so this takes about a second and doesn't re-run pose estimation. The skill version hash shown on the report changes.

## CLI and tests

```bash
python -m scripts.analyze_cli video.mp4 --markers markers.json --out report.json
python -m scripts.evidence data/<job_id> out_dir/     # bottom-frame stills for manual checking
python -m pytest -q
```

`markers.json` uses normalized coordinates: `{"t": 2.0, "bar": {"x": 0.65, "y": 0.45}, "heel": {"x": 0.33, "y": 0.78}, "toe": {"x": 0.42, "y": 0.79}}`.

## API

| Method | Path | |
|---|---|---|
| POST | `/api/jobs` | multipart `file`, optional `markers` (JSON string) → `{id}` |
| GET | `/api/jobs/{id}` | state, stage, progress, error; when done: `measurements`, `assessment` |
| POST | `/api/jobs/{id}/reassess` | re-apply the current skill |
| GET | `/api/jobs/{id}/video` | browser-safe H.264 copy |
| GET | `/api/jobs/{id}/annotated.mp4` | overlay burned in |
| GET | `/api/skill`, `/api/config` | skill contents, limits |
