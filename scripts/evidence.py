"""Dump bottom-of-rep frames with landmarks, depth line and bar point for manual verification.
Usage: python -m scripts.evidence data/<job_id> OUT_DIR"""
import json, os, sys
import cv2
d, out = sys.argv[1], sys.argv[2]; os.makedirs(out, exist_ok=True)
m = json.load(open(os.path.join(d, "measurements.json")))
a = json.load(open(os.path.join(d, "assessment.json")))
cap = cv2.VideoCapture(os.path.join(d, "video.mp4")); fps = cap.get(5)
W, H = m["video"]["width"], m["video"]["height"]
tiles = []
for r in m["reps"]:
    for key in ("bottom_t", "early_ascent_t"):
        t = r[key]; cap.set(cv2.CAP_PROP_POS_FRAMES, round(t * fps)); ok, img = cap.read()
        if not ok: continue
        f = min(m["frames"], key=lambda x: abs(x["t"] - t))
        P = lambda k: (int(f[k][0] * W), int(f[k][1] * H))
        ch = [k for k in ("ear", "shoulder", "hip", "knee", "ankle") if k in f]
        for k1, k2 in zip(ch, ch[1:]): cv2.line(img, P(k1), P(k2), (80, 220, 255), 2)
        for k in ch: cv2.circle(img, P(k), 5, (80, 220, 255), -1 if f[k][2] else 2)
        if "knee" in f: cv2.line(img, (0, P("knee")[1]), (W, P("knee")[1]), (255, 255, 255), 1)
        if "hip" in f: cv2.line(img, (0, P("hip")[1]), (W, P("hip")[1]), (0, 160, 255), 1)
        if "bar" in f: cv2.circle(img, P("bar"), 10, (0, 255, 255), 2)
        foot = m["markers"].get("foot")
        if foot: x = int(foot["mid"][0] * W); cv2.line(img, (x, 0), (x, H), (120, 255, 120), 1)
        res = {x["criterion_id"]: x["result"][:4] for x in a["evaluation"]["findings"] if x["rep"] == r["rep"]}
        lbl = f"rep{r['rep']} {key[:-2]} t={t:.2f} depth={r['metrics'].get('depth_margin')} back={r['metrics'].get('back_angle_bottom')}"
        cv2.putText(img, lbl, (8, 24), 0, 0.6, (0, 0, 0), 4); cv2.putText(img, lbl, (8, 24), 0, 0.6, (255, 255, 255), 1)
        p = os.path.join(out, f"rep{r['rep']}_{key[:-2]}.jpg"); cv2.imwrite(p, img); tiles.append(img)
if tiles:
    h = 480; row = [cv2.resize(t, (int(t.shape[1] * h / t.shape[0]), h)) for t in tiles]
    cv2.imwrite(os.path.join(out, "contact.jpg"), cv2.hconcat(row[:6]))
print("wrote", len(tiles))
