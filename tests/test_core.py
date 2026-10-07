"""Core behaviour tests: rules come from the skill, the AI cannot invent numbers.

Run: python -m pytest -q
"""
import copy
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import yaml

from app import agent, evaluate

SKILL = os.path.join(os.path.dirname(__file__), "..", "skill")


def fake_measurements(depth=0.05, back=45.0, view="side", conf=0.9):
    metrics = {
        "depth_margin": depth, "back_angle_bottom": back,
        "early_ascent_back_angle_change": 2.0, "early_ascent_hip_forward": 0.02,
        "bottom_dwell_s": 0.2, "head_pitch_down_bottom": 30.0, "ankle_rise": 0.01,
        "knee_forward_of_toe": 0.1, "bar_midfoot_max_offset": 0.1, "bar_horizontal_drift": 0.1,
        "bar_above_shoulder": 0.0,
    }
    return {
        "quality": {"view": view, "issues": []},
        "reps": [{"rep": 1, "bottom_t": 2.0, "start_t": 1.0, "end_t": 3.0, "early_ascent_t": 2.3,
                  "worst_bar_t": 2.1, "metrics": metrics, "notes": {},
                  "landmark_conf_at_bottom": {k: conf for k in ("shoulder", "hip", "knee", "ankle", "ear", "nose")},
                  "observations": {"hip_angle_bottom_deg": 60.0}}],
        "frames": [], "markers": {"bar": True, "foot": None},
    }


def result(ev, cid, rep=1):
    return next(f for f in ev["findings"] if f["criterion_id"] == cid and f["rep"] == rep)["result"]


@pytest.fixture
def skill_copy(monkeypatch):
    d = tempfile.mkdtemp()
    for f in ("criteria.yaml", "SKILL.md"):
        shutil.copy(os.path.join(SKILL, f), d)
    monkeypatch.setattr(evaluate, "SKILL_DIR", d)
    yield d
    shutil.rmtree(d)


def test_every_criterion_cites_pages():
    data = yaml.safe_load(open(os.path.join(SKILL, "criteria.yaml")))
    for c in data["criteria"]:
        assert c["source"], c["id"]
        for s in c["source"]:
            assert 1 <= s["page"] <= 35, (c["id"], s)
        for ch in c["checks"]:
            assert ch["tolerance_origin"] in ("document", "implementation_assumption")


def test_bands():
    ev = evaluate.evaluate(fake_measurements(depth=0.05))
    assert result(ev, "DEPTH") == "meets"
    ev = evaluate.evaluate(fake_measurements(depth=-0.3))
    assert result(ev, "DEPTH") == "does_not_meet"
    ev = evaluate.evaluate(fake_measurements(depth=-0.04))  # inside uncertainty band
    assert result(ev, "DEPTH") == "cannot_assess"


def test_low_confidence_and_view_gate():
    ev = evaluate.evaluate(fake_measurements(conf=0.3))
    assert result(ev, "DEPTH") == "cannot_assess"
    ev = evaluate.evaluate(fake_measurements(view="front_or_back"))
    assert all(f["result"] == "cannot_assess" for f in ev["findings"])
    ev = evaluate.evaluate(fake_measurements(view="oblique", depth=0.1))
    assert result(ev, "DEPTH") == "meets"                      # vertical-only: allowed
    assert result(ev, "BACK_ANGLE_BOTTOM") == "cannot_assess"  # angle: distorted


def test_low_bar_never_meets():
    ev = evaluate.evaluate(fake_measurements())
    assert result(ev, "LOW_BAR_POSITION") == "cannot_assess"


def test_rule_change_in_skill_changes_verdict(skill_copy):
    m = fake_measurements(back=40.0)
    assert result(evaluate.evaluate(m), "BACK_ANGLE_BOTTOM") == "meets"
    p = os.path.join(skill_copy, "criteria.yaml")
    data = yaml.safe_load(open(p))
    crit = next(c for c in data["criteria"] if c["id"] == "BACK_ANGLE_BOTTOM")
    crit["checks"][0]["meets_if"] = {"min": 43, "max": 47}
    crit["checks"][0]["fails_if_any"] = [{"lt": 42}, {"gt": 48}]
    yaml.safe_dump(data, open(p, "w"), sort_keys=False)
    assert result(evaluate.evaluate(m), "BACK_ANGLE_BOTTOM") == "does_not_meet"


def test_validator_rejects_invented_numbers_and_ids():
    m = fake_measurements(depth=-0.3)
    ev = evaluate.evaluate(m)
    raw = open(os.path.join(SKILL, "criteria.yaml")).read()
    llm = {
        "headline": "Depth was short on rep 1.",
        "summary": "Squat to depth.",
        "priorities": ["DEPTH", "BACK_ANGLE_BOTTOM"],   # second one did not fail
        "findings": [
            {"rep": 1, "criterion_id": "DEPTH", "explanation": "Your hips were -0.3 thigh lengths from the knee.",
             "feedback": "Go deeper.", "uncertainty": ""},
            {"rep": 1, "criterion_id": "BACK_ANGLE_BOTTOM", "explanation": "Aim for 37.5 degrees per research.",
             "feedback": "x", "uncertainty": ""},                            # invented number
            {"rep": 9, "criterion_id": "MADE_UP", "explanation": "", "feedback": "", "uncertainty": ""},
        ],
    }
    out = agent.validate_and_merge(m, ev, json.dumps(llm), raw)
    by = {f["criterion_id"]: f for f in out["findings"]}
    assert by["DEPTH"]["source"] == "agent"
    assert by["BACK_ANGLE_BOTTOM"]["source"] == "template"
    assert out["priorities"] == ["DEPTH"]
    log = " ".join(out["validation"])
    assert "37.5" in log and "MADE_UP" in log and "BACK_ANGLE_BOTTOM" in log


def test_agent_cannot_flip_result():
    m = fake_measurements(depth=-0.3)
    ev = evaluate.evaluate(m)
    raw = open(os.path.join(SKILL, "criteria.yaml")).read()
    llm = {"findings": [{"rep": 1, "criterion_id": "DEPTH", "result": "meets", "explanation": "Fine.",
                         "feedback": "Good.", "uncertainty": ""}]}
    agent.validate_and_merge(m, ev, json.dumps(llm), raw)
    assert result(ev, "DEPTH") == "does_not_meet"   # results live only in the evaluation
