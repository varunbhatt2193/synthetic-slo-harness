from __future__ import annotations

import json

from slo_harness.metrics import MetricsBuffer


def test_record_probe_emits_success_duration_and_steps():
    buffer = MetricsBuffer(source="test")
    buffer.record_probe(target="saucedemo", probe="journey", success=True, duration_s=3.2,
                        steps={"login": 1.1, "add_to_cart": 0.4})
    series = {ts.name: ts for ts in buffer.to_timeseries()}
    assert series["synthetic_probe_success"].samples[0][0] == 1.0
    assert series["synthetic_probe_success"].labels == {
        "target": "saucedemo", "probe": "journey", "source": "test",
    }
    assert series["synthetic_probe_duration_seconds"].samples[0][0] == 3.2
    step_series = [ts for ts in buffer.to_timeseries()
                   if ts.name == "synthetic_journey_step_duration_seconds"]
    assert {ts.labels["step"] for ts in step_series} == {"login", "add_to_cart"}


def test_points_with_same_name_and_labels_group_into_one_series():
    buffer = MetricsBuffer(source="test")
    buffer.gauge("m", 1.0, ts_ms=1000, target="a")
    buffer.gauge("m", 2.0, ts_ms=2000, target="a")
    buffer.gauge("m", 3.0, ts_ms=3000, target="b")
    series = buffer.to_timeseries()
    assert len(series) == 2
    by_target = {ts.labels["target"]: ts for ts in series}
    assert by_target["a"].samples == [(1.0, 1000), (2.0, 2000)]


def test_cron_jitter_uses_workflow_tick_not_probe_start(monkeypatch):
    # 1787512320 is 8 min past a :04-offset grid point; the probe itself may start minutes
    # later (checkout, uv sync, browser install) and must not contaminate the measurement.
    monkeypatch.setenv("PROBE_TICK_EPOCH", "1787512320")
    cron = MetricsBuffer(source="cron")
    cron.record_cron_jitter()
    by_name = {ts.name: ts for ts in cron.to_timeseries()}
    assert by_name["synthetic_cron_jitter_seconds"].samples[0][0] == (1787512320 - 240) % 900
    # The mod-period jitter above is capped at 899 s and cannot see skipped ticks; the raw
    # tick epoch is what makes multi-hour scheduler starvation measurable as a PromQL gap.
    assert by_name["synthetic_cron_tick_timestamp_seconds"].samples[0][0] == 1787512320


def test_cron_jitter_skipped_without_tick_or_for_non_cron(monkeypatch):
    monkeypatch.setenv("PROBE_TICK_EPOCH", "1787512320")
    quiet = MetricsBuffer(source="local")
    quiet.record_cron_jitter()
    assert len(quiet) == 0

    monkeypatch.delenv("PROBE_TICK_EPOCH")
    cron = MetricsBuffer(source="cron")
    cron.record_cron_jitter()
    assert len(cron) == 0  # no reference timestamp: record nothing, not a wrong number


def test_push_skipped_without_credentials(monkeypatch):
    for var in ("GRAFANA_PUSH_URL", "GRAFANA_PUSH_USER", "GRAFANA_PUSH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    buffer = MetricsBuffer(source="test")
    buffer.gauge("m", 1.0)
    assert buffer.push_from_env() is False


def test_dump_jsonl(tmp_path):
    buffer = MetricsBuffer(source="test")
    buffer.gauge("m", 1.5, ts_ms=1234, target="a")
    out = tmp_path / "points.jsonl"
    buffer.dump_jsonl(out)
    (line,) = out.read_text().splitlines()
    assert json.loads(line) == {
        "name": "m", "labels": {"source": "test", "target": "a"}, "value": 1.5, "ts_ms": 1234,
    }
