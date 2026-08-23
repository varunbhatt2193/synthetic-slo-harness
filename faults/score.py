"""Score the monitoring against injected ground truth.

Inputs: the fault timeline (ground truth, written by inject.py) and the probe records
(written by probe_loop.py). Optionally, Grafana alert-state annotations fetched over the
Grafana HTTP API, so the same run scores two layers:

    probe-level  — when did a probe first *see* the fault (breaching cycle)?
    alert-level  — when did an alert actually *fire*?

Definitions (fixed here, not tuned per run):
    breaching cycle   any check failed, or latency above --latency-slo-ms
    detection episode ≥ --consecutive breaching cycles in a row (default 2 — one bad cycle
                      is a blip, two is a signal; the same debounce a paging rule would use)
    MTTD              episode start − fault window start
    true positive     episode starting inside a fault window (+ --grace-s after it ends,
                      covering in-flight damage after the toxic is removed)
    precision         TP episodes / all episodes        recall = detected faults / faults
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx


@dataclass
class Episode:
    start: float
    end: float
    cycles: int


def _fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%H:%M:%S")


def _fmt_mttd(seconds: float | None) -> str:
    return "never detected" if seconds is None else f"{seconds / 60:.1f} min"


def load_cycles(records_path: Path, latency_slo_ms: float) -> list[tuple[float, bool]]:
    """Collapse per-check records into (cycle_ts, breaching) pairs, oldest first."""
    cycles: dict[float, bool] = {}
    with records_path.open() as f:
        for line in f:
            record = json.loads(line)
            cycle = record.get("cycle", record["ts"])
            breach = (not record["ok"]) or record["latency_ms"] > latency_slo_ms
            cycles[cycle] = cycles.get(cycle, False) or breach
    return sorted(cycles.items())


def find_episodes(cycles: list[tuple[float, bool]], consecutive: int) -> list[Episode]:
    episodes: list[Episode] = []
    run: list[float] = []
    for ts, breaching in cycles:
        if breaching:
            run.append(ts)
        else:
            if len(run) >= consecutive:
                episodes.append(Episode(start=run[0], end=run[-1], cycles=len(run)))
            run = []
    if len(run) >= consecutive:
        episodes.append(Episode(start=run[0], end=run[-1], cycles=len(run)))
    return episodes


def fetch_alert_firings(grafana_url: str, token: str, window: tuple[float, float]) -> list[float]:
    """Timestamps (epoch s) of alert-state annotations entering an alerting state."""
    response = httpx.get(
        f"{grafana_url.rstrip('/')}/api/annotations",
        params={"type": "alert", "from": int(window[0] * 1000), "to": int(window[1] * 1000),
                "limit": 500},
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )
    response.raise_for_status()
    firings = []
    for annotation in response.json():
        if "alerting" in str(annotation.get("newState", "")).lower():
            firings.append(annotation["time"] / 1000)
    return sorted(firings)


def score(timeline: dict, episodes: list[Episode], alert_firings: list[float] | None,
          grace_s: float) -> dict:
    windows = timeline["fault_windows"]
    rows = []
    matched_episodes: set[int] = set()
    for window in windows:
        lo, hi = window["start"], window["end"] + grace_s
        probe_mttd = None
        for i, episode in enumerate(episodes):
            if lo <= episode.start <= hi:
                probe_mttd = episode.start - lo
                matched_episodes.add(i)
                break
        alert_mttd = None
        if alert_firings is not None:
            firing = next((t for t in alert_firings if lo <= t <= hi + grace_s), None)
            alert_mttd = None if firing is None else firing - lo
        rows.append({"fault": window["fault_type"], "start": lo,
                     "probe_mttd_s": probe_mttd, "alert_mttd_s": alert_mttd})

    false_positives = [e for i, e in enumerate(episodes) if i not in matched_episodes]
    tp = len(matched_episodes)
    return {
        "rows": rows,
        "false_positive_episodes": len(false_positives),
        "precision": tp / len(episodes) if episodes else None,
        "recall": sum(r["probe_mttd_s"] is not None for r in rows) / len(windows)
        if windows else None,
        "episodes": len(episodes),
    }


def to_markdown(fault_type: str, result: dict, latency_slo_ms: float, consecutive: int,
                alerts_scored: bool) -> str:
    lines = [
        f"## Fault evaluation scorecard — `{fault_type}`",
        "",
        f"Detection rule (frozen before the run): ≥{consecutive} consecutive breaching cycles; "
        f"a cycle breaches on any failed check or latency > {latency_slo_ms:.0f} ms.",
        "",
        "| fault window (UTC) | fault | probe MTTD | alert MTTD |",
        "|---|---|---|---|",
    ]
    for row in result["rows"]:
        alert_cell = _fmt_mttd(row["alert_mttd_s"]) if alerts_scored else "_not scored_"
        lines.append(f"| {_fmt_ts(row['start'])} | {row['fault']} | "
                     f"{_fmt_mttd(row['probe_mttd_s'])} | {alert_cell} |")
    precision = "n/a" if result["precision"] is None else f"{result['precision']:.2f}"
    recall = "n/a" if result["recall"] is None else f"{result['recall']:.2f}"
    lines += [
        "",
        f"- detection episodes: **{result['episodes']}** "
        f"(false positives: **{result['false_positive_episodes']}**)",
        f"- precision: **{precision}**, recall: **{recall}**",
    ]
    if not alerts_scored:
        lines.append("- alert-level MTTD not scored: no Grafana credentials in the environment")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeline", type=Path, default=Path("timeline.json"))
    parser.add_argument("--records", type=Path, default=Path("probe_records.jsonl"))
    parser.add_argument("--latency-slo-ms", type=float, default=2000.0)
    parser.add_argument("--consecutive", type=int, default=2)
    parser.add_argument("--grace-s", type=float, default=60.0)
    parser.add_argument("--grafana-url", default=os.environ.get("GRAFANA_URL", ""))
    parser.add_argument("--grafana-token", default=os.environ.get("GRAFANA_API_TOKEN", ""))
    parser.add_argument("--out", type=Path, default=Path("scorecard.md"))
    args = parser.parse_args()

    timeline = json.loads(args.timeline.read_text())
    cycles = load_cycles(args.records, args.latency_slo_ms)
    episodes = find_episodes(cycles, args.consecutive)

    alert_firings = None
    if args.grafana_url and args.grafana_token and cycles:
        try:
            alert_firings = fetch_alert_firings(
                args.grafana_url, args.grafana_token,
                (cycles[0][0] - 60, cycles[-1][0] + args.grace_s + 120),
            )
        except httpx.HTTPError as exc:
            print(f"[score] could not fetch Grafana annotations, scoring probes only: {exc}")

    result = score(timeline, episodes, alert_firings, args.grace_s)
    markdown = to_markdown(timeline["fault_type"], result, args.latency_slo_ms,
                           args.consecutive, alerts_scored=alert_firings is not None)

    args.out.write_text(markdown)
    print(markdown)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a") as f:
            f.write(markdown)


if __name__ == "__main__":
    main()
