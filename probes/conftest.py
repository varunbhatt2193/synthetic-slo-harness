"""Probe fixtures: target parametrization, timing capture, and metrics push.

Every probe test records exactly one `synthetic_probe_success` / `_duration_seconds` pair
(plus per-step timings for journeys). At session end the buffer is pushed to Grafana Cloud
(if credentials are in the environment) and always dumped to probe-results/points.jsonl so a
run leaves scoreable evidence behind even with no network to Grafana.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from slo_harness.metrics import MetricsBuffer
from slo_harness.targets import load_targets

RESULTS_DIR = Path("probe-results")


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--target", action="store", default=None,
                     help="probe only the named target from targets.yml")


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    only = metafunc.config.getoption("--target")
    for fixture, kind in (("api_target", "api"), ("journey_target", "journey")):
        if fixture in metafunc.fixturenames:
            targets = load_targets(kind=kind, name=only)
            metafunc.parametrize(fixture, targets, ids=[t.name for t in targets])


@pytest.fixture(scope="session")
def metrics() -> MetricsBuffer:
    buffer = MetricsBuffer()
    buffer.record_cron_jitter()
    yield buffer
    buffer.dump_jsonl(RESULTS_DIR / "points.jsonl")
    buffer.push_from_env()


class ProbeRecorder:
    """Times a probe and its named steps; reports to the session metrics buffer on exit."""

    def __init__(self, buffer: MetricsBuffer, target: str, probe: str) -> None:
        self._buffer = buffer
        self._target = target
        self._probe = probe
        self.steps: dict[str, float] = {}

    @contextmanager
    def step(self, name: str):
        started = time.monotonic()
        yield
        self.steps[name] = time.monotonic() - started


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    """Expose each phase's outcome on the item so the recorder fixture can read it."""
    report = yield
    setattr(item, f"probe_report_{report.when}", report)
    return report


@pytest.fixture
def probe_recorder(metrics: MetricsBuffer, request: pytest.FixtureRequest):
    """Yields a recorder; success is whatever the test outcome says it is."""
    target = None
    for fixture_name in ("api_target", "journey_target"):
        if fixture_name in request.fixturenames:
            target = request.getfixturevalue(fixture_name)
    assert target is not None, "probe tests must use an api_target or journey_target fixture"

    recorder = ProbeRecorder(metrics, target.name, target.kind)
    started = time.monotonic()
    yield recorder
    call_report = getattr(request.node, "probe_report_call", None)
    metrics.record_probe(
        target=target.name,
        probe=target.kind,
        success=call_report is not None and call_report.passed,
        duration_s=time.monotonic() - started,
        steps=recorder.steps,
    )
