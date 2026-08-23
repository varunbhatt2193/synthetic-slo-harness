# Grafana assets — SLO definition and alert rules, as code

Committed here so the alert thresholds are **frozen and reviewable before any scored fault
run** (see the protocol in `docs/spec.md`). `scripts/provision_grafana.py` applies everything
to a Grafana Cloud stack idempotently.

## The SLO (frozen)

- **SLI:** probe availability — `avg(synthetic_probe_success{source="cron"})` per target.
  One sample per target per 15-minute cron tick.
- **SLO: 99% of probe runs succeed over a rolling 30 days.** Error budget = 1% of runs
  (~28 failed runs/month at the */15 cadence).
- Latency is dashboarded (p50/p95, per-step) but not part of the alerting SLO in round 1.

## Burn-rate windows — adapted, and why

The SRE-workbook multiwindow pairs assume scrape-rate sampling. At 4 samples/hour a 5-minute
window holds ≤1 sample, so the standard pairs shift up one tier:

| rule | windows (long, gated by short) | burn-rate threshold | severity |
|---|---|---|---|
| fast burn | 1h, gated by 30m | > 14.4 | `page` |
| slow burn | 6h, gated by 3h | > 6 | `ticket` |
| fault-eval burn | 5m, gated by 2m | > 14.4 | `page` |

The `fault-eval` rule exists because eval-run probes fire every 20 s, not every 15 min — it
is what makes **alert-level MTTD** measurable inside a 10-minute fault window. Its
`noDataState` is OK because the `fault-eval` target only emits during an eval run.

Any change to these thresholds after scored runs exist is a new round, reported as such.

## Files

- `dashboard.json` — availability, error budget, burn rates, latency p50/p95, journey step
  timings, cron jitter, probe staleness. `__PROM_DS_UID__` is substituted at provision time.
- `alert-rules.json` — the three rules above, in Grafana provisioning-API shape with fixed
  UIDs so re-provisioning updates in place.
