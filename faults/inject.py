"""Fault timeline runner: degrade the toy service on a known schedule and record ground truth.

Topology (all inside one Actions job, as service containers — see fault-eval.yml):

    probe_loop.py ─► toxiproxy "front" :8080 ─► API :8000 ─► toxiproxy "db" :5433 ─► postgres

Fault classes:
    latency_ramp — latency toxic on the front proxy, stepped 500 → 1500 → 3000 ms across
                   the fault window; crosses the latency SLO partway through, on purpose,
                   so MTTD for this class includes the time the ramp spends below the SLO.
    error_burst  — the db proxy is disabled; the API stays up and returns 503s (deep health and
                   authorize both fail, shallow health stays green — exactly the failure a
                   shallow check misses).
    hard_outage  — the front proxy is disabled; connections are refused outright.

The timeline (timeline.json) is written after every event so a crashed run still leaves
ground truth behind. Everything in it is UTC epoch seconds.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from slo_harness.toxiproxy import ToxiproxyClient  # noqa: E402

RAMP_STEPS_MS = [500, 1500, 3000]


class Timeline:
    def __init__(self, fault_type: str, out: Path) -> None:
        self.fault_type = fault_type
        self.out = out
        self.events: list[dict] = []
        self.fault_windows: list[dict] = []

    def event(self, action: str, **params) -> float:
        ts = time.time()
        self.events.append({"ts": ts, "action": action, **({"params": params} if params else {})})
        print(f"[inject] {action} at {ts:.1f} {params or ''}", flush=True)
        self.flush()
        return ts

    def flush(self) -> None:
        tmp = self.out.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "fault_type": self.fault_type,
            "events": self.events,
            "fault_windows": self.fault_windows,
        }, indent=2))
        tmp.replace(self.out)


def ensure_proxies(client: ToxiproxyClient, api_upstream: str, db_upstream: str) -> None:
    client.create_proxy("front", listen="0.0.0.0:8080", upstream=api_upstream)
    client.create_proxy("db", listen="0.0.0.0:5433", upstream=db_upstream)


def apply_fault(client: ToxiproxyClient, timeline: Timeline, fault: str, duration_s: float) -> None:
    start = timeline.event("fault_start", fault=fault)
    if fault == "latency_ramp":
        step_s = duration_s / len(RAMP_STEPS_MS)
        client.add_toxic("front", name="ramp", kind="latency",
                         attributes={"latency": RAMP_STEPS_MS[0], "jitter": 50})
        timeline.event("ramp_step", latency_ms=RAMP_STEPS_MS[0])
        time.sleep(step_s)
        for latency_ms in RAMP_STEPS_MS[1:]:
            client.update_toxic("front", "ramp", {"latency": latency_ms, "jitter": 50})
            timeline.event("ramp_step", latency_ms=latency_ms)
            time.sleep(step_s)
        client.remove_toxic("front", "ramp")
    elif fault == "error_burst":
        client.set_enabled("db", False)
        time.sleep(duration_s)
        client.set_enabled("db", True)
    elif fault == "hard_outage":
        client.set_enabled("front", False)
        time.sleep(duration_s)
        client.set_enabled("front", True)
    else:
        raise SystemExit(f"unknown fault type: {fault}")
    end = timeline.event("fault_end", fault=fault)
    timeline.fault_windows.append({"start": start, "end": end, "fault_type": fault})
    timeline.flush()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fault", required=True,
                        choices=["latency_ramp", "error_burst", "hard_outage"])
    parser.add_argument("--baseline-min", type=float, default=5.0)
    parser.add_argument("--fault-min", type=float, default=10.0)
    parser.add_argument("--recovery-min", type=float, default=5.0)
    parser.add_argument("--toxiproxy-url", default="http://localhost:8474")
    parser.add_argument("--api-upstream", default="api:8000")
    parser.add_argument("--db-upstream", default="postgres:5432")
    parser.add_argument("--out", type=Path, default=Path("timeline.json"))
    args = parser.parse_args()

    client = ToxiproxyClient(args.toxiproxy_url)
    client.wait_until_ready()
    ensure_proxies(client, args.api_upstream, args.db_upstream)

    timeline = Timeline(args.fault, args.out)
    try:
        timeline.event("baseline_start")
        time.sleep(args.baseline_min * 60)
        apply_fault(client, timeline, args.fault, args.fault_min * 60)
        timeline.event("recovery_start")
        time.sleep(args.recovery_min * 60)
        timeline.event("eval_end")
    finally:
        client.reset()  # never leave a toxic behind, even on crash/cancel
        timeline.flush()
        client.close()


if __name__ == "__main__":
    main()
