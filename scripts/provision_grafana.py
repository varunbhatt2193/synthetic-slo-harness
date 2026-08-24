"""Provision the Grafana stack from the committed assets, idempotently.

    GRAFANA_URL=https://<stack>.grafana.net GRAFANA_SA_TOKEN=<token> \
        uv run python scripts/provision_grafana.py

Creates/updates: the "Synthetic SLO Harness" folder, the dashboard from
grafana/dashboard.json, and the three alert rules from grafana/alert-rules.json (fixed UIDs,
so re-running updates in place — thresholds still only change via a reviewed commit).

The service-account token needs Admin on the stack (folders + dashboards + alert
provisioning). The Prometheus datasource UID is discovered, not configured.

Deliberately NOT provisioned: the webhook contact point and notification policy. The GitHub
PAT it needs should not transit more systems than necessary, and the custom payload template
is a two-minute UI step — see docs/setup-grafana.md §3.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
FOLDER_UID = "synthetic-slo"
FOLDER_TITLE = "Synthetic SLO Harness"


def die(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    base_url = os.environ.get("GRAFANA_URL", "").rstrip("/")
    token = os.environ.get("GRAFANA_SA_TOKEN", "")
    if not base_url or not token:
        die("set GRAFANA_URL (https://<stack>.grafana.net) and GRAFANA_SA_TOKEN")

    client = httpx.Client(
        base_url=base_url,
        timeout=30.0,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            # keep provisioned objects editable in the UI for exploration; the repo
            # copy stays the source of truth on the next run
            "X-Disable-Provenance": "true",
        },
    )

    # 1. Discover the stack's Prometheus datasource.
    response = client.get("/api/datasources")
    if response.status_code == 401:
        die("token rejected (401) — needs a service account token with Admin role")
    response.raise_for_status()
    prom = next((d for d in response.json() if d["type"] == "prometheus"), None)
    if prom is None:
        die("no Prometheus datasource on this stack — is this the right Grafana URL?")
    prom_uid = prom["uid"]
    print(f"prometheus datasource: {prom['name']} (uid {prom_uid})")

    # 2. Folder.
    response = client.post("/api/folders", json={"uid": FOLDER_UID, "title": FOLDER_TITLE})
    if response.status_code in (409, 412):  # Grafana signals "already exists" with either
        print(f"folder {FOLDER_UID}: already exists")
    else:
        response.raise_for_status()
        print(f"folder {FOLDER_UID}: created")

    def substitute(text: str) -> str:
        return text.replace("__PROM_DS_UID__", prom_uid).replace("__FOLDER_UID__", FOLDER_UID)

    # 3. Dashboard.
    dashboard = json.loads(substitute((REPO_ROOT / "grafana" / "dashboard.json").read_text()))
    response = client.post(
        "/api/dashboards/db",
        json={"dashboard": dashboard, "folderUid": FOLDER_UID, "overwrite": True},
    )
    response.raise_for_status()
    print(f"dashboard: {base_url}{response.json().get('url', '')}")

    # 4. Alert rules (fixed UIDs: POST once, PUT thereafter).
    rules = json.loads(substitute((REPO_ROOT / "grafana" / "alert-rules.json").read_text()))
    for rule in rules["rules"]:
        uid = rule["uid"]
        response = client.put(f"/api/v1/provisioning/alert-rules/{uid}", json=rule)
        if response.status_code == 404:
            response = client.post("/api/v1/provisioning/alert-rules", json=rule)
            action = "created"
        else:
            action = "updated"
        if response.status_code >= 400:
            die(f"alert rule {uid}: HTTP {response.status_code}: {response.text[:300]}")
        print(f"alert rule {uid}: {action}")

    print("\ndone. remaining manual step: webhook contact point + notification policy "
          "(docs/setup-grafana.md §3)")


if __name__ == "__main__":
    main()
