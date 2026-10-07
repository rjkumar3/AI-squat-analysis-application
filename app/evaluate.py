"""Applies skill/criteria.yaml to measured metrics. Deterministic; no LLM."""
from __future__ import annotations

import hashlib
import os

import yaml

SKILL_DIR = os.environ.get("SKILL_DIR", os.path.join(os.path.dirname(__file__), "..", "skill"))
OBS_CONF = 0.5

MEETS, FAILS, CANNOT = "meets", "does_not_meet", "cannot_assess"


_skill_cache: dict = {}


def load_skill():
    """Parsed skill, cached until criteria.yaml or SKILL.md changes on disk."""
    path = os.path.join(SKILL_DIR, "criteria.yaml")
    md_path = os.path.join(SKILL_DIR, "SKILL.md")
    stamp = tuple((st.st_mtime_ns, st.st_size) for st in (os.stat(path), os.stat(md_path)))
    hit = _skill_cache.get((path, md_path))
    if hit and hit[0] == stamp:
        return hit[1]
    with open(path, "rb") as fh:
        raw = fh.read()
    data = yaml.safe_load(raw)
    data["_hash"] = hashlib.sha256(raw).hexdigest()[:10]
    with open(md_path, encoding="utf-8") as fh:
        skill_md = fh.read()
    out = (data, raw.decode("utf-8"), skill_md)
    _skill_cache[(path, md_path)] = (stamp, out)
    return out


def _band_text(check):
    m = check.get("meets_if")
    parts = []
    if m is None:
        parts.append("never awarded 'meets' (not verifiable on video)")
    else:
        if "min" in m and "max" in m:
            parts.append(f"meets if {m['min']} to {m['max']}")
        elif "min" in m:
            parts.append(f"meets if >= {m['min']}")
        elif "max" in m:
            parts.append(f"meets if <= {m['max']}")
    fails = []
    for c in check.get("fails_if_any") or []:
        if "lt" in c:
            fails.append(f"< {c['lt']}")
        if "gt" in c:
            fails.append(f"> {c['gt']}")
    if fails:
        parts.append("fails if " + " or ".join(fails))
    parts.append("otherwise borderline")
    return "; ".join(parts)


def apply_check(check, value):
    if value is None:
        return CANNOT
    for c in check.get("fails_if_any") or []:
        if "lt" in c and value < c["lt"]:
            return FAILS
        if "gt" in c and value > c["gt"]:
            return FAILS
    m = check.get("meets_if")
    if m is not None:
        if ("min" not in m or value >= m["min"]) and ("max" not in m or value <= m["max"]):
            return MEETS
    return CANNOT


def evaluate(measurements: dict):
    skill, _, _ = load_skill()
    q = measurements["quality"]
    view = q["view"]
    findings = []
    for rep in measurements["reps"]:
        for crit in skill["criteria"]:
            f = {
                "rep": rep["rep"], "criterion_id": crit["id"], "criterion": crit["name"],
                "assessable": crit.get("assessable"),
                "sources": crit.get("source", []),
                "t": rep["bottom_t"], "values": [], "reason": None,
            }
            if crit["id"].startswith("BAR_") and rep.get("worst_bar_t") is not None:
                f["t"] = rep["worst_bar_t"]
            if crit.get("phase") == "early_ascent":
                f["t"] = rep.get("early_ascent_t") or rep["bottom_t"]

            reason = None
            valid_views = crit.get("valid_views") or ["side"]
            if view not in valid_views:
                reason = ("The camera is not square to the side of the lifter, so this measurement would be distorted."
                          if view == "oblique" else "The video is not a side view, so this cannot be measured.")
            for need in crit.get("requires") or []:
                if reason:
                    break
                key = "bar_marker" if need == "bar_marker" else "foot_markers"
                note = rep["notes"].get(key)
                if note:
                    reason = note
            if not reason:
                low = [(lm, rep["landmark_conf_at_bottom"].get(lm)) for lm in crit.get("required_landmarks") or []]
                low = [(lm, c) for lm, c in low if c is None or c < OBS_CONF]
                if low:
                    reason = "Low tracking confidence for " + ", ".join(
                        f"{lm} ({c:.2f})" if c is not None else lm for lm, c in low) + " at the bottom."

            results = []
            for check in crit["checks"]:
                val = rep["metrics"].get(check["metric"])
                res = CANNOT if reason else apply_check(check, val)
                f["values"].append({
                    "metric": check["metric"], "value": val, "unit": check.get("unit"),
                    "band": _band_text(check), "tolerance_origin": check.get("tolerance_origin"),
                    "tolerance_note": check.get("tolerance_note", "").strip(), "check_result": res,
                })
                results.append(res)
                if val is None and not reason:
                    reason = "The measurement could not be computed for this rep."
            if FAILS in results:
                result = FAILS
            elif results and all(r == MEETS for r in results):
                result = MEETS
            else:
                result = CANNOT
                if not reason:
                    reason = "Borderline: the measurement is inside the uncertainty band, so the app will not call it either way."
            f["result"] = result
            f["reason"] = reason if result == CANNOT else None
            fb = crit.get("feedback", {})
            f["template_feedback"] = fb.get(result, "")
            f["why_partial"] = (crit.get("why_partial") or "").strip()
            findings.append(f)

    summary = {MEETS: 0, FAILS: 0, CANNOT: 0}
    for f in findings:
        summary[f["result"]] += 1
    return {
        "skill_version": skill.get("version"), "skill_hash": skill["_hash"],
        "criteria": [{"id": c["id"], "name": c["name"], "assessable": c.get("assessable"),
                      "sources": c.get("source", [])} for c in skill["criteria"]],
        "not_assessable": skill.get("not_assessable_from_side_view", []),
        "findings": findings, "counts": summary,
    }
