"""Probe metric collection.

Probes record gauges into a `MetricsBuffer`; at the end of a probe run the buffer is pushed to
Grafana Cloud via remote_write and dumped as JSONL for artifacts and offline scoring. Push
credentials come from the environment so the same probes run locally (no creds → skip push,
keep the JSONL) and in Actions (creds from repo secrets).

Metric names (series count is deliberately tiny — tens, against a 10k free-tier budget):

    synthetic_probe_success{target, probe, source}                 1|0
    synthetic_probe_duration_seconds{target, probe, source}
    synthetic_journey_step_duration_seconds{target, step, source}
    synthetic_cron_jitter_seconds{source}                          actual vs scheduled cron start
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

from .remote_write import RemoteWriteClient, TimeSeries

log = logging.getLogger("slo_harness.metrics")

ENV_URL = "GRAFANA_PUSH_URL"
ENV_USER = "GRAFANA_PUSH_USER"
ENV_TOKEN = "GRAFANA_PUSH_TOKEN"
ENV_SOURCE = "PROBE_SOURCE"

CRON_PERIOD_S = 15 * 60  # probe.yml runs `*/15`; jitter is measured against that grid


@dataclass(frozen=True)
class Point:
    name: str
    labels: tuple[tuple[str, str], ...]
    value: float
    ts_ms: int


class MetricsBuffer:
    def __init__(self, source: str | None = None) -> None:
        self.source = source or os.environ.get(ENV_SOURCE, "local")
        self._points: list[Point] = []

    def gauge(self, name: str, value: float, ts_ms: int | None = None, **labels: str) -> None:
        labels["source"] = self.source
        self._points.append(
            Point(
                name=name,
                labels=tuple(sorted(labels.items())),
                value=float(value),
                ts_ms=ts_ms if ts_ms is not None else int(time.time() * 1000),
            )
        )

    def record_probe(
        self, *, target: str, probe: str, success: bool, duration_s: float,
        steps: dict[str, float] | None = None,
    ) -> None:
        self.gauge("synthetic_probe_success", 1.0 if success else 0.0, target=target, probe=probe)
        self.gauge("synthetic_probe_duration_seconds", duration_s, target=target, probe=probe)
        for step, seconds in (steps or {}).items():
            self.gauge(
                "synthetic_journey_step_duration_seconds", seconds, target=target, step=step
            )

    def record_cron_jitter(self) -> None:
        """Seconds since the last */15 grid point. Only meaningful when source == cron."""
        if self.source == "cron":
            self.gauge("synthetic_cron_jitter_seconds", time.time() % CRON_PERIOD_S)

    def to_timeseries(self) -> list[TimeSeries]:
        grouped: dict[tuple[str, tuple[tuple[str, str], ...]], list[tuple[float, int]]] = {}
        for p in self._points:
            grouped.setdefault((p.name, p.labels), []).append((p.value, p.ts_ms))
        return [
            TimeSeries(name=name, labels=dict(labels), samples=samples)
            for (name, labels), samples in grouped.items()
        ]

    def push_from_env(self) -> bool:
        """Push to remote_write if credentials are configured; returns whether a push happened."""
        url, user, token = (os.environ.get(k, "") for k in (ENV_URL, ENV_USER, ENV_TOKEN))
        if not (url and user and token):
            log.warning("remote_write credentials not set (%s/%s/%s); skipping push",
                        ENV_URL, ENV_USER, ENV_TOKEN)
            return False
        client = RemoteWriteClient(url, user, token)
        try:
            client.push(self.to_timeseries())
        finally:
            client.close()
        return True

    def dump_jsonl(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            for p in self._points:
                f.write(json.dumps(
                    {"name": p.name, "labels": dict(p.labels), "value": p.value, "ts_ms": p.ts_ms}
                ) + "\n")

    def __len__(self) -> int:
        return len(self._points)
