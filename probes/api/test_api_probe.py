"""HTTP API probe: availability, latency, and a shallow response-shape check."""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.probe

REQUEST_TIMEOUT_S = 15.0


def test_api_probe(api_target, probe_recorder):
    with probe_recorder.step("request"):
        response = httpx.get(
            api_target.url, timeout=REQUEST_TIMEOUT_S, follow_redirects=True,
            headers={"User-Agent": "slo-harness-probe/0.1"},
        )

    assert response.status_code == api_target.expect_status, (
        f"{api_target.name}: expected {api_target.expect_status}, got {response.status_code}"
    )

    if api_target.expect_json_key:
        with probe_recorder.step("parse"):
            body = response.json()
        assert api_target.expect_json_key in body, (
            f"{api_target.name}: key {api_target.expect_json_key!r} missing from response"
        )
