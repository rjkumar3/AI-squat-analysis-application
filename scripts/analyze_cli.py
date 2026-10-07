"""CLI: python -m scripts.analyze_cli VIDEO [--markers markers.json] [--out report.json]"""
import argparse, json, os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import pipeline

ap = argparse.ArgumentParser()
ap.add_argument("video"); ap.add_argument("--markers"); ap.add_argument("--out", default="report.json")
ap.add_argument("--no-agent", action="store_true")
a = ap.parse_args()
markers = json.load(open(a.markers)) if a.markers else None
t0 = time.time()
tmp = os.path.join(tempfile.mkdtemp(), "v.mp4"); pipeline.transcode(a.video, tmp)
m = pipeline.run(tmp, markers, stage=lambda n, p: None)
res = pipeline.assess(m, use_agent=not a.no_agent)
json.dump({"measurements": m, **res}, open(a.out, "w"), indent=1)
print(f"done in {time.time()-t0:.1f}s; reps={len(m['reps'])} view={m['quality']['view']} counts={res['evaluation']['counts']}")
for r in m["reps"]: print(r["rep"], r["bottom_t"], r["metrics"])
print(res["coaching"]["headline"])
