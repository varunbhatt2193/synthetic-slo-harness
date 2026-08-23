"""A small client for the Toxiproxy HTTP API.

Adapted from the pytest-ai-triage project (github.com/varunbhatt2193/pytest-ai-triage), which
uses the same toy payments service behind Toxiproxy; extended here with runtime proxy
enable/disable and toxic updates, which the fault timeline runner needs mid-run.
"""

from __future__ import annotations

import time

import httpx


class ToxiproxyClient:
    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self._http = httpx.Client(base_url=base_url, timeout=timeout)

    def wait_until_ready(self, timeout: float = 60.0) -> None:
        deadline = time.time() + timeout
        last: Exception | None = None
        while time.time() < deadline:
            try:
                self._http.get("/version").raise_for_status()
                return
            except Exception as exc:  # connection refused while the process boots
                last = exc
                time.sleep(0.25)
        raise RuntimeError(f"toxiproxy did not become ready within {timeout}s: {last}")

    def create_proxy(self, name: str, listen: str, upstream: str) -> dict:
        response = self._http.post(
            "/proxies",
            json={"name": name, "listen": listen, "upstream": upstream, "enabled": True},
        )
        if response.status_code == 409:  # already created by an earlier session
            return self._http.get(f"/proxies/{name}").raise_for_status().json()
        response.raise_for_status()
        return response.json()

    def set_enabled(self, proxy: str, enabled: bool) -> dict:
        response = self._http.post(f"/proxies/{proxy}", json={"enabled": enabled})
        response.raise_for_status()
        return response.json()

    def add_toxic(
        self,
        proxy: str,
        *,
        name: str,
        kind: str,
        stream: str = "downstream",
        attributes: dict | None = None,
        toxicity: float = 1.0,
    ) -> dict:
        response = self._http.post(
            f"/proxies/{proxy}/toxics",
            json={
                "name": name,
                "type": kind,
                "stream": stream,
                "toxicity": toxicity,
                "attributes": attributes or {},
            },
        )
        response.raise_for_status()
        return response.json()

    def update_toxic(self, proxy: str, name: str, attributes: dict) -> dict:
        response = self._http.post(
            f"/proxies/{proxy}/toxics/{name}", json={"attributes": attributes}
        )
        response.raise_for_status()
        return response.json()

    def remove_toxic(self, proxy: str, name: str) -> None:
        self._http.delete(f"/proxies/{proxy}/toxics/{name}").raise_for_status()

    def reset(self) -> None:
        """Remove every toxic and re-enable every proxy."""
        self._http.post("/reset").raise_for_status()

    def close(self) -> None:
        self._http.close()
