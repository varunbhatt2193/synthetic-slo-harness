// Cloudflare Worker: external scheduler fallback for GHA cron starvation.
//
// Fires on the same 15-minute grid as probe.yml's own schedule (see wrangler.toml) and
// dispatches the probe workflow with as_cron=true, so runs land as source="cron" /
// scheduler="external". Measured motivation: on 2026-08-24 GitHub delivered ~11-15% of
// scheduled ticks to this repo; details in the README "Limits" section.
//
// The GitHub PAT lives in a Worker secret (wrangler secret put GITHUB_PAT) — encrypted at
// rest, never in this repo. Failures are surfaced by the Grafana heartbeat alert
// (slo-probe-heartbeat), which watches probe outcomes and therefore catches a dead pinger,
// an expired PAT, and GHA-wide starvation with one rule.

const REPO = "varunbhatt2193/synthetic-slo-harness";
const WORKFLOW = "probe.yml";

export default {
  async scheduled(event, env, ctx) {
    const res = await fetch(
      `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${env.GITHUB_PAT}`,
          Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
          "User-Agent": "synthetic-slo-pinger",
        },
        // workflow_dispatch inputs are string-typed over the REST API; GHA coerces
        // "true" back to boolean for a `type: boolean` input.
        body: JSON.stringify({ ref: "main", inputs: { as_cron: "true" } }),
      },
    );
    if (res.status !== 204) {
      // Visible in the Worker's Cloudflare logs; the real alerting is the Grafana
      // heartbeat, which fires when ticks stop arriving for any reason.
      console.error(`dispatch failed: HTTP ${res.status}: ${await res.text()}`);
    }
  },
};
