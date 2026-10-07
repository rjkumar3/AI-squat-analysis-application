"""Coaching layer: Claude explains the deterministic verdicts using the skill.

The model never decides a result. Its output is validated:
  * criterion ids / reps must exist in the evaluation
  * every number it writes must appear in the report or the skill
Anything that fails validation is replaced by the skill's template text and the
replacement is recorded in `validation` so it is visible in the UI.
"""
from __future__ import annotations

import json
import os
import re

from .evaluate import CANNOT, FAILS, MEETS, load_skill

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
NUM_RE = re.compile(r"(?<![A-Za-z_])-?\d+(?:\.\d+)?")


def fmt_t(t):
    if t is None:
        return "?"
    m, s = divmod(float(t), 60)
    return f"{int(m)}:{s:04.1f}"


def _fmt_val(v):
    if v is None:
        return "n/a"
    return f"{v:+.2f}" if abs(v) < 10 else f"{v:.1f}"


def _explain(f):
    parts = []
    for v in f["values"]:
        name = v["metric"].replace("_", " ")
        origin = "document number" if v["tolerance_origin"] == "document" else "app's own tolerance"
        parts.append(f"{name}: {_fmt_val(v['value'])} {v['unit'] or ''} ({v['band']}; {origin}).")
    base = " ".join(parts)
    if f["result"] == CANNOT and f.get("reason"):
        base = f["reason"] + " " + base
    return base.strip()


def template_coach(measurements, ev, mode="template", note=None):
    reps = measurements["reps"]
    findings = []
    for f in ev["findings"]:
        unc = []
        if f["result"] != CANNOT and any(v["tolerance_origin"] == "implementation_assumption" for v in f["values"]):
            unc.append("The pass/fail band is the app's assumption; the document gives the direction, not a number.")
        if f["result"] != CANNOT and f.get("why_partial"):
            unc.append(f["why_partial"])
        findings.append({
            "rep": f["rep"], "criterion_id": f["criterion_id"],
            # measurements are shown verbatim by the UI; template mode adds no restatement
            "explanation": "", "feedback": f["template_feedback"], "uncertainty": " ".join(unc),
            "source": "template",
        })
    fail_counts = {}
    for f in ev["findings"]:
        if f["result"] == FAILS:
            fail_counts[f["criterion_id"]] = fail_counts.get(f["criterion_id"], 0) + 1
    order = [c["id"] for c in ev["criteria"]]
    priorities = sorted(fail_counts, key=lambda k: (-fail_counts[k], order.index(k)))[:3]
    names = {c["id"]: c["name"] for c in ev["criteria"]}
    c = ev["counts"]
    nrep = f"{len(reps)} rep" + ("" if len(reps) == 1 else "s")
    if not reps:
        headline = "No complete squat repetitions were found in this video."
    elif priorities:
        headline = f"{nrep} analysed. Main fix: {names[priorities[0]].split(':')[0].lower()}."
    elif c[MEETS] > 0:
        headline = f"{nrep} analysed with no clear faults against the checks this video supports."
    else:
        headline = f"{nrep} found, but this video doesn't support most checks."
    q_errors = [i["message"] for i in measurements["quality"]["issues"] if i["level"] == "error"]
    summary = (f"Across all reps, {c[MEETS]} checks met the standard, {c[FAILS]} did not, and {c[CANNOT]} could not be assessed. ")
    if q_errors:
        summary = q_errors[0] + " " + summary
    if priorities:
        summary += f"Start with: {names[priorities[0]]}."
    return {"mode": mode, "model": None, "headline": headline, "summary": summary.strip(),
            "priorities": priorities, "findings": findings, "validation": [note] if note else []}


def _allowed_numbers(measurements, ev, criteria_raw):
    allowed = set()
    for m in NUM_RE.findall(criteria_raw):
        allowed.add(float(m))
    for f in ev["findings"]:
        allowed.add(float(f["rep"]))
        if f["t"] is not None:
            allowed.add(round(f["t"], 1))
            mm, ss = divmod(f["t"], 60)
            allowed.update({float(int(mm)), round(ss, 1), float(int(ss))})
        for v in f["values"]:
            if v["value"] is not None:
                allowed.add(float(v["value"]))
    for r in measurements["reps"]:
        for v in (r.get("observations") or {}).values():
            if v is not None:
                allowed.add(float(v))
    allowed.update(float(i) for i in range(0, 11))
    allowed.add(float(len(measurements["reps"])))
    for k in ("meets", "does_not_meet", "cannot_assess"):
        allowed.add(float(ev["counts"].get(k, 0)))
    return allowed


def _numbers_ok(text, allowed):
    bad = []
    for m in NUM_RE.findall(text or ""):
        x = float(m)
        if any(abs(x - v) <= max(0.011, 0.012 * abs(v), 0.51 if abs(v) >= 10 else 0) for v in allowed):
            continue
        bad.append(m)
    return bad


def _compact_report(measurements, ev):
    return {
        "quality": {"view": measurements["quality"]["view"],
                    "issues": [i["message"] for i in measurements["quality"]["issues"]]},
        "reps": [{"rep": r["rep"], "bottom_time": fmt_t(r["bottom_t"]), "observations": r["observations"]}
                 for r in measurements["reps"]],
        "counts": ev["counts"],
        "findings": [{
            "rep": f["rep"], "criterion_id": f["criterion_id"], "result": f["result"],
            "time": fmt_t(f["t"]), "reason_cannot_assess": f["reason"],
            "measurements": [{"metric": v["metric"], "value": v["value"], "unit": v["unit"],
                              "band": v["band"], "tolerance_origin": v["tolerance_origin"]} for v in f["values"]],
        } for f in ev["findings"]],
    }


def coach(measurements, ev):
    if not measurements["reps"]:
        return template_coach(measurements, ev)
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return template_coach(measurements, ev, note="ANTHROPIC_API_KEY not set: feedback text comes from the skill's templates, not the AI agent.")
    _, criteria_raw, skill_md = load_skill()
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=key)
        msg = client.messages.create(
            model=MODEL, max_tokens=8000,
            system=skill_md + "\n\n## criteria.yaml\n\n```yaml\n" + criteria_raw + "\n```",
            messages=[{"role": "user", "content":
                       "Here is the analysis report. Write the coaching JSON as specified.\n\n```json\n"
                       + json.dumps(_compact_report(measurements, ev)) + "\n```"}],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        return validate_and_merge(measurements, ev, text, criteria_raw)
    except Exception as e:  # network, auth, parse - never block the user
        return template_coach(measurements, ev, note=f"AI agent unavailable ({type(e).__name__}); template feedback shown.")


def validate_and_merge(measurements, ev, llm_text, criteria_raw):
    base = template_coach(measurements, ev, mode="agent")
    base["model"] = MODEL
    log = []
    t = llm_text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        out = json.loads(t[t.find("{"): t.rfind("}") + 1])
    except Exception:
        base["mode"] = "template"
        base["validation"] = ["Agent output was not valid JSON; template feedback shown."]
        return base
    allowed = _allowed_numbers(measurements, ev, criteria_raw)
    valid_pairs = {(f["rep"], f["criterion_id"]) for f in ev["findings"]}
    by_pair = {}
    for item in out.get("findings", []):
        pair = (item.get("rep"), item.get("criterion_id"))
        if pair not in valid_pairs:
            log.append(f"Dropped agent finding for unknown rep/criterion {pair}.")
            continue
        bad = _numbers_ok(" ".join(str(item.get(k, "")) for k in ("explanation", "feedback", "uncertainty")), allowed)
        if bad:
            log.append(f"Rep {pair[0]} {pair[1]}: agent text used numbers not in the report or skill ({', '.join(bad[:4])}); replaced with template.")
            continue
        by_pair[pair] = item
    merged = []
    for f in base["findings"]:
        item = by_pair.get((f["rep"], f["criterion_id"]))
        if item:
            merged.append({**f, "explanation": item.get("explanation", f["explanation"]),
                           "feedback": item.get("feedback", f["feedback"]),
                           "uncertainty": item.get("uncertainty", ""), "source": "agent"})
        else:
            merged.append(f)
    missing = len(base["findings"]) - len(by_pair)
    if missing:
        log.append(f"{missing} finding(s) use template text (missing or rejected from agent output).")
    base["findings"] = merged
    for k in ("headline", "summary"):
        v = out.get(k)
        if isinstance(v, str) and v.strip():
            bad = _numbers_ok(v, allowed)
            if bad:
                log.append(f"Agent {k} used unsupported numbers ({', '.join(bad[:4])}); template used.")
            else:
                base[k] = v.strip()
    failed = {f["criterion_id"] for f in ev["findings"] if f["result"] == FAILS}
    pr = [p for p in out.get("priorities", []) if p in failed][:3]
    dropped = [p for p in out.get("priorities", []) if p not in failed]
    if dropped:
        log.append(f"Removed priorities that did not fail: {', '.join(map(str, dropped))}.")
    if pr or not failed:
        base["priorities"] = pr
    base["validation"] = log
    return base
