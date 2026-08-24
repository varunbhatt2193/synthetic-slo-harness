# External scheduler fallback (Cloudflare Worker)

GHA cron is best-effort, and on this repo it under-delivered badly (measured: ~11–15% of
scheduled ticks on 2026-08-24 — see the main README's Limits section). This Worker restores
a reliable 15-minute grid by dispatching `probe.yml` with `as_cron=true` from Cloudflare's
cron triggers, which are materially more punctual. Runs it fires are labeled
`source="cron"` (so SLO continuity is preserved) and `scheduler="external"` (so GHA-cron
jitter evidence is never contaminated — jitter metrics are only recorded for
`scheduler="gha"`).

Cost: $0. Cloudflare's free plan includes Workers cron triggers and requires no card.

## One-time setup

1. **Cloudflare account** — free plan at <https://dash.cloudflare.com/sign-up>, no card.

2. **Fine-grained GitHub PAT** — <https://github.com/settings/personal-access-tokens/new>:
   - Repository access: *Only select repositories* → `synthetic-slo-harness`
   - Permissions: **Actions: Read and write** — nothing else
   - Expiration: 90 days (calendar the rotation; when it expires, the Grafana
     `slo-probe-heartbeat` alert is what will tell you)
   - Blast-radius note: Actions read/write can dispatch *any* workflow in this repo
     (including `build-image`, which pushes to GHCR), cancel/re-run jobs, and read run
     logs. That is the narrowest scope GitHub offers for workflow dispatch.

3. **Deploy** (from this directory):

   ```bash
   cd pinger
   npx wrangler login          # opens the browser for Cloudflare OAuth
   npx wrangler deploy
   npx wrangler secret put GITHUB_PAT   # paste the PAT at the prompt
   ```

4. **Verify**: within 15 minutes a `workflow_dispatch` run of `probe` appears in the
   Actions tab, its job summary says `source=cron`, and the Grafana dashboard's
   availability panels tick. `npx wrangler tail` streams the Worker's logs live if the
   dispatch is failing.

## Monitoring the monitor's monitor

Do not add another pinger to watch this one. The Grafana rule `slo-probe-heartbeat`
watches *outcomes* — "cron-labeled probe ticks are arriving" — so a dead Worker, an
expired PAT, and GHA-wide starvation all trip the same alert.
