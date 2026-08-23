"""Scoring logic, exercised on synthetic runs where the right answer is arithmetic."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "faults"))

from score import Episode, find_episodes, load_cycles, score  # noqa: E402

T0 = 1_724_000_000.0


def _cycles(pattern: str, start: float = T0, interval: float = 20.0):
    """'..XX.' → ok, ok, breach, breach, ok at 20 s spacing."""
    return [(start + i * interval, c == "X") for i, c in enumerate(pattern)]


def test_single_blip_is_not_an_episode():
    assert find_episodes(_cycles("..X.."), consecutive=2) == []


def test_consecutive_breaches_form_one_episode():
    (episode,) = find_episodes(_cycles("..XXX."), consecutive=2)
    assert episode.start == T0 + 2 * 20
    assert episode.cycles == 3


def test_trailing_run_still_counts():
    (episode,) = find_episodes(_cycles("...XX"), consecutive=2)
    assert episode.cycles == 2


def test_mttd_and_recall_for_detected_fault():
    fault_start = T0 + 100
    timeline = {"fault_windows": [
        {"start": fault_start, "end": fault_start + 300, "fault_type": "error_burst"}
    ]}
    episodes = [Episode(start=fault_start + 45, end=fault_start + 200, cycles=8)]
    result = score(timeline, episodes, alert_firings=None, grace_s=60)
    assert result["rows"][0]["probe_mttd_s"] == 45
    assert result["recall"] == 1.0
    assert result["precision"] == 1.0
    assert result["false_positive_episodes"] == 0


def test_undetected_fault_and_false_positive():
    timeline = {"fault_windows": [
        {"start": T0 + 1000, "end": T0 + 1300, "fault_type": "hard_outage"}
    ]}
    # one episode long before the fault window: a false positive, and the fault goes unseen
    episodes = [Episode(start=T0 + 100, end=T0 + 160, cycles=3)]
    result = score(timeline, episodes, alert_firings=None, grace_s=60)
    assert result["rows"][0]["probe_mttd_s"] is None
    assert result["recall"] == 0.0
    assert result["precision"] == 0.0
    assert result["false_positive_episodes"] == 1


def test_alert_mttd_uses_first_firing_in_window():
    fault_start = T0
    timeline = {"fault_windows": [
        {"start": fault_start, "end": fault_start + 600, "fault_type": "latency_ramp"}
    ]}
    episodes = [Episode(start=fault_start + 40, end=fault_start + 500, cycles=20)]
    firings = [fault_start - 500, fault_start + 180, fault_start + 400]
    result = score(timeline, episodes, alert_firings=firings, grace_s=60)
    assert result["rows"][0]["alert_mttd_s"] == 180


def test_load_cycles_marks_latency_breach_and_groups_checks(tmp_path):
    records = tmp_path / "records.jsonl"
    rows = [
        {"ts": T0 + 0.1, "cycle": T0, "check": "health_deep", "ok": True, "latency_ms": 50},
        {"ts": T0 + 0.4, "cycle": T0, "check": "authorize", "ok": True, "latency_ms": 2500},
        {"ts": T0 + 20.1, "cycle": T0 + 20, "check": "health_deep", "ok": True, "latency_ms": 60},
        {"ts": T0 + 20.3, "cycle": T0 + 20, "check": "authorize", "ok": True, "latency_ms": 70},
    ]
    records.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    cycles = load_cycles(records, latency_slo_ms=2000)
    assert cycles == [(T0, True), (T0 + 20, False)]
