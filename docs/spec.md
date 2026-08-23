# Portfolio Project Spec — Synthetic Monitoring & SLO Harness (GitHub Actions-native)

Project #4. Build order stays: Playwright suite → triage harness → this one, with transit running in
parallel for the DE file. ~2 weekends, and it deliberately reuses the triage project's toy service and
Toxiproxy layer, so it gets cheaper the later it's built.

---

## The one-line pitch

> Synthetic monitoring with the tools an SDET already owns — Playwright journeys and API probes
> scheduled by GitHub Actions, feeding Grafana/Prometheus SLO dashboards with burn-rate alerting —
> then **the monitoring itself gets tested**: inject faults on a known timeline and score
> time-to-detect and alert precision.

## Why this project

1. **Observability/monitoring is claimed-adjacent but never shown.** Charter has Splunk log
   validation, Visa has "alerting on drift or record loss" — but the skills section has no Prometheus,
   no Grafana, no SLO vocabulary, and no bullet where he *built* monitoring. This adds the missing
   keywords with evidence attached.
2. **It makes GitHub Actions the product, not the afterthought.** Everywhere else GHA just runs the
   tests. Here the workflows ARE the system — cron scheduling, matrix fan-out, service containers,
   reusable workflows, a published composite action. "Hands-on GitHub Actions" stops being a skills-line
   claim.
3. **SRE vocabulary is senior signal.** SLOs, error budgets, multiwindow burn rates, MTTD, alert
   precision — this is the language of "owns quality in production," which is where senior SDET reqs
   are drifting.
4. **Playwright-as-probe is a real industry pattern** (it is literally Checkly's product and Grafana
   Synthetic Monitoring's browser checks). Using his flagship tool in a second discipline reads as
   depth, not repetition.

## What makes this a project and not a tutorial

Status-page-via-Actions exists (upptime), and hosted synthetics exist (Checkly, Grafana SM, Datadog).
Do not rebuild them and do not claim novelty for pinging URLs. The project is the part none of them
publish: **measured alert quality**. Because the fault timeline is injected and known, the monitoring
gets a scorecard — detection time per fault class, false positives per quiet week, alert
precision/recall. Same signature as the other three projects: *inject known faults, measure detection.*
If time runs short, cut probe types, never the scoring.

## Architecture

```
GitHub Actions (the platform)
├── probe.yml — cron every 15 min, matrix over targets × probe types
│     pytest-playwright browser journeys + httpx API checks
│     → step timings, availability, latency → Prometheus remote_write
├── fault-eval.yml — workflow_dispatch(fault_type, duration)
│     toy payments API + Toxiproxy as **service containers** inside the job
│     fault script degrades on a known timeline while probes run against it
│     → scores MTTD / precision vs the timeline → job summary + artifact
└── alert-issues.yml — Grafana webhook → auto-file GitHub issue, auto-close on recovery

Grafana Cloud free tier (no servers to run)
├── Prometheus: availability, p50/p95 latency, journey step timings, probe-suite health
├── SLO dashboards: error budget remaining, burn rate
└── Alerting: multiwindow burn-rate rules (fast-burn page, slow-burn ticket)
```

- **Continuous targets:** his own GitHub Pages status page plus one public automation-practice demo
  app at a polite cadence (every 15 min, one journey — do not hammer third-party sites).
- **Fault-eval target:** the triage project's toy payments API behind Toxiproxy, spun up as service
  containers *inside the workflow run* — self-contained, nothing deployed, nothing paid for.
- **Verify current Grafana Cloud free-tier limits and remote_write auth before building** — tiers
  change; don't build from memory.

## The GitHub Actions hands-on inventory

Exercise these deliberately — this list is the "hands-on GHA" evidence, and each should be visible in
the repo:

- Scheduled workflows (cron) and its honest limitation — see measurement section
- `workflow_dispatch` with typed inputs (fault type, duration)
- Matrix strategy (targets × probe types × browser)
- **Service containers** (the fault-eval stack)
- Reusable workflows (`workflow_call`) shared by probe.yml and fault-eval.yml
- **A published composite action** — `playwright-synthetic-probe`: run a journey, emit metrics.
  Marketplace listing = a standalone, checkable GHA credential and a second repo link
- Concurrency groups (don't overlap probe runs), caching (browsers/deps), artifacts (traces on
  failure), `$GITHUB_STEP_SUMMARY` (per-run scorecards), environments + secrets (Grafana keys),
  issue automation via `GITHUB_TOKEN`

## What to measure

| Metric | Definition |
|---|---|
| MTTD per fault class | minutes from injected fault start to firing alert — latency ramp, error burst, hard outage |
| Alert precision / recall | alerts that corresponded to a real injected fault vs noise; faults that never alerted |
| False positives per quiet week | alert count across ≥7 days with no injected faults |
| Burn-rate rule comparison | fast-burn vs slow-burn windows: which fired first, which flapped |
| Scheduler jitter | actual GHA cron firing delay, p50/p95 — this sets the floor on MTTD |
| Probe-suite health | flake rate and duration trend of the probes themselves (monitoring the monitor) |

**The jitter finding is a feature, not an embarrassment.** GHA cron is best-effort and can slip
minutes; measuring it and stating "Actions-native monitoring has a detection floor of ~X min, here's
when that's acceptable and when you buy Checkly" is exactly the judgment call a senior candidate is
paid for. Publish it.

## Build order — ~2 weekends

**Weekend 1, Saturday:** repo + composite-action skeleton; pytest-playwright journey probe + httpx
probe running locally; Grafana Cloud wired via remote_write; first dashboard panel.
**Weekend 1, Sunday:** probe.yml on cron with matrix; step timings and availability recorded; status
page on GitHub Pages; a week of quiet-baseline data collection starts tonight — it has to run in the
background before false-positive numbers mean anything.
**Weekend 2, Saturday:** SLO + error-budget dashboard; multiwindow burn-rate alert rules; alert →
GitHub issue automation with auto-close.
**Weekend 2, Sunday:** fault-eval.yml — service containers, fault timeline script, MTTD/precision
scoring in the job summary; README tables; scheduler-jitter analysis from the accumulated cron data.

## Making it honest

- Freeze alert thresholds **before** the scored fault-injection runs; tuning after seeing results and
  re-scoring must be reported as round 2, not silently merged.
- The quiet-week false-positive number requires an actual quiet week of runtime — don't compress it.
- Publish raw probe data and the fault timelines so every number in the README is recomputable.
- Disclose what this is not: not multi-region, not sub-minute resolution, not a Checkly replacement —
  the README's "limits" section is part of the credibility.
- Reuse of the triage project's toy service is stated openly — shared components across a portfolio
  is engineering economy, not padding, but it should be visible, not discovered.

## Resume bullets

No numbers until measured; in-progress phrasing until it ships:

```
Synthetic Monitoring & SLO Harness        Personal project, 2026
• Built GitHub Actions-native synthetic monitoring: scheduled Playwright journey and
  API probes on a cron matrix exporting availability and latency metrics to
  Prometheus/Grafana Cloud, with SLO error budgets and multiwindow burn-rate alerts
  that auto-file and auto-close GitHub issues.
• Tested the monitoring itself: injected latency ramps, error bursts and hard outages
  on a known timeline into a containerized target running as an Actions service
  container, scoring time-to-detect and alert precision against ground truth.
• Published the probe runner as a reusable composite action, with reusable workflows,
  service containers, caching and per-run scorecards in job summaries.
```

Once measured: MTTD and false-positive numbers into bullet 2, jitter finding into the interview story,
repo + marketplace links on the file.

## Optional extensions

- **OpenTelemetry traces** (an hour or two): emit a span per journey step via OTLP to Grafana Cloud
  Tempo; one screenshot of a slow-step trace adds the OTel keyword with evidence.
- **Loki** for probe logs alongside metrics — completes the three-pillars story if a req leans on it.
- **Public status page** polish on GitHub Pages fed by probe data — the visible, clickable artifact
  for non-engineers.
