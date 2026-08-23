# Grafana Cloud setup

One-time wiring between this repo and a Grafana Cloud free-tier stack. Verified against
Grafana Cloud as of 2026-08: free tier includes 10k active metric series and 14-day
retention; this project uses a few dozen series.

## 1. remote_write credentials (metrics push)

1. In the [Grafana Cloud portal](https://grafana.com/profile/org), open your stack → the
   **Prometheus** card → **Details**.
2. Note the **remote write endpoint** (`https://prometheus-<cluster>.grafana.net/api/prom/push`)
   and the **username / instance ID** (a numeric ID).
3. Generate a **Cloud Access Policy token** with the `metrics:write` scope (the Prometheus
   card offers "Generate now").
4. Add three **repository secrets** (Settings → Secrets and variables → Actions):

| secret | value |
|---|---|
| `GRAFANA_PUSH_URL` | the `/api/prom/push` endpoint |
| `GRAFANA_PUSH_USER` | the numeric metrics instance ID |
| `GRAFANA_PUSH_TOKEN` | the access policy token (`metrics:write`) |

Smoke test: run the **probe** workflow manually (Actions → probe → Run workflow), then in
Grafana Explore query `synthetic_probe_success`.

## 2. Alert webhook → GitHub issues

`alert-issues.yml` listens for a `repository_dispatch` event with type `grafana-alert`.
GitHub requires that request body to be exactly `{"event_type": ..., "client_payload": ...}`,
so the Grafana webhook contact point needs a **custom payload template** (supported in
current Grafana Cloud alerting):

1. Create a fine-grained GitHub PAT limited to this repo with **Contents: read-write**
   permission (repository_dispatch is triggered via the contents API).
2. Grafana → Alerting → Contact points → New:
   - Integration: **Webhook**
   - URL: `https://api.github.com/repos/varunbhatt2193/synthetic-slo-harness/dispatches`
   - HTTP method: POST
   - Authorization header — scheme `Bearer`, credentials: the PAT
   - **Custom payload template**:

```
{
  "event_type": "grafana-alert",
  "client_payload": {
    "status": "{{ .Status }}",
    "alertname": "{{ (index .Alerts 0).Labels.alertname }}",
    "summary": "{{ (index .Alerts 0).Annotations.summary }}",
    "fingerprint": "{{ (index .Alerts 0).Fingerprint }}"
  }
}
```

3. Point the notification policy for this project's alert rules at that contact point.

## 3. Alert-level MTTD scoring (optional, used by fault-eval)

`faults/score.py` can also fetch alert-state annotations to score when alerts actually
fired. Add two more repo secrets:

| secret | value |
|---|---|
| `GRAFANA_URL` | your stack URL, e.g. `https://<stack>.grafana.net` |
| `GRAFANA_API_TOKEN` | a service account token with Viewer role (annotations read) |

Without them, fault-eval still runs and scores probe-level detection only.

## 4. Repository settings

- `git config core.hooksPath .githooks` after cloning (author-identity guard).
- Actions → General → Workflow permissions: read-only is fine (`alert-issues.yml` and
  `build-image.yml` request their own scopes explicitly).
