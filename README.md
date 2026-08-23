# synthetic-slo-harness

GitHub Actions-native synthetic monitoring — Playwright journeys and API probes on a cron
matrix, feeding Grafana Cloud SLO dashboards with burn-rate alerting — and then the part
hosted synthetics vendors don't publish: **the monitoring itself gets tested.** Faults are
injected into a containerized target on a known timeline, and the monitoring is scored
against that ground truth: time-to-detect per fault class, alert precision/recall, false
positives per quiet week.

**Status: scaffolded.** Probes, metrics pipeline, fault injection and scoring are built and
unit-tested; quiet-week baseline collection and the first scored fault runs are next. No
measured numbers are claimed yet.

## Architecture

```
GitHub Actions (the platform, not the afterthought)
├── probe.yml ── cron */15 ─► run-probes.yml (reusable) ─► playwright-synthetic-probe
│                             matrix: targets × probe kinds       (composite action)
│     Playwright journey (saucedemo checkout) + httpx API check (githubstatus)
│     → availability, latency, per-step timings → Prometheus remote_write
├── fault-eval.yml ── workflow_dispatch(fault_type, durations)
│     toy payments API + Toxiproxy + Postgres as service containers in the job
│     inject.py degrades on a known timeline ── probe_loop.py watches through the proxy
│     → score.py: MTTD / precision vs ground truth → job summary + raw artifacts
└── alert-issues.yml ── Grafana webhook → repository_dispatch → auto-file / auto-close issues

Grafana Cloud free tier (no servers to run)
├── Prometheus: synthetic_probe_success / _duration_seconds / journey step timings / cron jitter
├── SLO dashboards: error budget remaining, burn rate            (weekend 2)
└── Alerting: multiwindow burn-rate rules (fast-burn / slow-burn) (weekend 2)
```

### Fault classes and what they exercise

| fault | mechanism | what it tests |
|---|---|---|
| `latency_ramp` | latency toxic on the front proxy, stepped 500→1500→3000 ms | latency SLO detection as degradation crosses the threshold mid-ramp |
| `error_burst` | DB proxy disabled; API stays up, returns 503s | the failure a shallow `/health` check cannot see |
| `hard_outage` | front proxy disabled; connections refused | plain availability detection |

### What gets measured

| metric | definition |
|---|---|
| MTTD per fault class | minutes from injected fault start to detection (probe-level and alert-level, scored separately) |
| Alert precision / recall | detection episodes matching an injected fault window vs noise; faults that never alerted |
| False positives per quiet week | detection episodes across ≥7 days with no injected faults |
| Scheduler jitter | `synthetic_cron_jitter_seconds` — actual GHA cron delay vs the */15 grid; this sets the floor on MTTD for cron-driven probes |
| Probe-suite health | flake rate and duration trend of the probes themselves |

The detection rule is frozen in `faults/score.py` (≥2 consecutive breaching cycles; a cycle
breaches on a failed check or latency over the SLO) — before any scored run, per the protocol
in [docs/spec.md](docs/spec.md). Threshold tuning after seeing results gets reported as a
second round, never merged silently.

## Repo tour

- `probes/` — the probes, as pytest tests. `conftest.py` times each probe and its named steps,
  then pushes gauges via remote_write and dumps JSONL evidence.
- `src/slo_harness/` — the library: a hand-rolled minimal Prometheus remote_write encoder
  (unit-tested against an independent decoder), the metrics buffer, target registry, and a
  small Toxiproxy client.
- `faults/` — `inject.py` (ground-truth timeline runner), `probe_loop.py` (tight-loop probe
  during evals), `score.py` (MTTD/precision scorecard).
- `service/` — the toy payments API, reused from
  [pytest-ai-triage](https://github.com/varunbhatt2193/pytest-ai-triage) where it plays the
  same role: a service written to be broken in known ways. Shared components across a
  portfolio are engineering economy; the reuse is deliberate and disclosed.
- `.github/actions/playwright-synthetic-probe/` — the composite action every probe run goes
  through; destined for its own repo and a Marketplace listing.

## Running locally

```bash
uv sync
uv run pytest                      # unit tests (no network)
uv run pytest probes --target githubstatus         # one live API probe
uv run playwright install chromium
uv run pytest probes --target saucedemo --browser chromium   # one live journey

# fault-eval end to end, compressed timeline:
docker compose up -d --build
uv run python faults/inject.py --fault error_burst --baseline-min 1 --fault-min 1 --recovery-min 1 &
uv run python faults/probe_loop.py --duration-min 3 --interval-s 10
uv run python faults/score.py
```

Without `GRAFANA_PUSH_*` in the environment, probes skip the metrics push and still write
`probe-results/points.jsonl` — local runs need no credentials.

## Setup (one-time)

Grafana Cloud wiring — remote_write credentials, the alert webhook → repository_dispatch
custom payload, and required repo secrets — is documented in
[docs/setup-grafana.md](docs/setup-grafana.md).

## Limits (read before quoting numbers)

- **GHA cron is best-effort.** Firing can slip minutes; that jitter is measured and published
  rather than hidden, and it is the detection floor for the cron path. Sub-minute detection
  requires the in-job probe loop (fault evals) or a paid scheduler — this is exactly the
  boundary where you buy Checkly/Grafana SM instead.
- Single region (GitHub-hosted runners), no geographic diversity.
- Grafana Cloud free tier: 14-day metric retention — quiet-week analysis must be run inside
  that window.
- Not a product: two targets at a polite 15-minute cadence, chosen to demonstrate the method,
  not to monitor the internet.
