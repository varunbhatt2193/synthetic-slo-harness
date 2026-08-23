"""Tight-loop API probe used during fault evaluation.

GHA cron cannot fire sub-minute, so during a fault-eval run the probes run in-process on a
fixed interval instead (default 20 s). Each cycle checks deep health and performs one real
authorization round-trip through the front proxy, records the result to JSONL (the scorer's
input), and pushes the same signal to Grafana Cloud so the alert rules see the fault too —
that is what lets a single run score both probe-level and alert-level detection.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from slo_harness.metrics import MetricsBuffer  # noqa: E402

CHECK_TIMEOUT_S = 5.0


def run_cycle(base_url: str) -> list[dict]:
    """One probe cycle: deep health + a real authorize. Returns one record per check."""
    records = []
    with httpx.Client(base_url=base_url, timeout=CHECK_TIMEOUT_S) as client:
        for check, fn in (
            ("health_deep", lambda: client.get("/health/deep")),
            ("authorize", lambda: client.post(
                "/authorize",
                json={"card_last4": "4242", "amount_cents": 1999, "currency": "USD"},
                headers={"Idempotency-Key": f"probe-{uuid.uuid4().hex}"},
            )),
        ):
            started = time.monotonic()
            ts = time.time()
            try:
                response = fn()
                ok = response.status_code < 400
                status = response.status_code
            except httpx.HTTPError:
                ok, status = False, None
            records.append({
                "ts": ts, "check": check, "ok": ok, "status": status,
                "latency_ms": (time.monotonic() - started) * 1000,
            })
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8080")
    parser.add_argument("--interval-s", type=float, default=20.0)
    parser.add_argument("--duration-min", type=float, required=True)
    parser.add_argument("--out", type=Path, default=Path("probe_records.jsonl"))
    parser.add_argument("--target-label", default="fault-eval")
    args = parser.parse_args()

    deadline = time.time() + args.duration_min * 60
    args.out.parent.mkdir(parents=True, exist_ok=True)

    while time.time() < deadline:
        cycle_start = time.time()
        records = run_cycle(args.url)
        for record in records:
            record["cycle"] = cycle_start  # scorer groups checks back into cycles by this

        with args.out.open("a") as f:
            for record in records:
                f.write(json.dumps(record) + "\n")

        buffer = MetricsBuffer()
        for record in records:
            buffer.gauge("synthetic_probe_success", 1.0 if record["ok"] else 0.0,
                         target=args.target_label, probe=record["check"])
            buffer.gauge("synthetic_probe_duration_seconds", record["latency_ms"] / 1000,
                         target=args.target_label, probe=record["check"])
        try:
            buffer.push_from_env()
        except Exception as exc:  # a failed push must not stop the evidence loop
            print(f"[probe_loop] push failed, continuing: {exc}", file=sys.stderr, flush=True)

        ok_flags = ",".join(f"{r['check']}={'ok' if r['ok'] else 'FAIL'}" for r in records)
        print(f"[probe_loop] {cycle_start:.0f} {ok_flags}", flush=True)
        time.sleep(max(0.0, args.interval_s - (time.time() - cycle_start)))


if __name__ == "__main__":
    main()
