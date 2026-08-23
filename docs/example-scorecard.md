# Example scorecard output

This is real output from `faults/score.py`, produced by one **compressed local smoke run**
(docker compose stack, 1-minute phases, 5-second probe interval) used to verify the pipeline
end to end. It is committed here so the scorecard format is visible without running anything.

**It is not a claimed measurement.** The protocol-compliant numbers — full-length fault
windows on GitHub Actions runners, alert-level MTTD scored against live Grafana alerts, and a
real quiet week behind the false-positive count — will replace the README's "no measured
numbers" line when they exist, with raw `timeline.json` / `probe_records.jsonl` artifacts
published alongside.

---

## Fault evaluation scorecard — `error_burst`

Detection rule (frozen before the run): ≥2 consecutive breaching cycles; a cycle breaches on any failed check or latency > 2000 ms.

| fault window (UTC) | fault | probe MTTD | alert MTTD |
|---|---|---|---|
| 19:25:46 | error_burst | 0.0 min | _not scored_ |

- detection episodes: **1** (false positives: **0**)
- precision: **1.00**, recall: **1.00**
- alert-level MTTD not scored: no Grafana credentials in the environment

---

*Note on "0.0 min": at a 5-second probe interval with a 2-cycle debounce, detection lands
seconds after injection — that is the compressed smoke setup, not a claim about the 15-minute
cron path, whose detection floor is set by scheduler jitter (see README limits).*
