"""Minimal Prometheus remote_write (v1) client.

The remote_write wire format is a snappy-block-compressed protobuf `WriteRequest`. The schema is
four tiny messages, so rather than pull in `protobuf` plus generated stubs (or an unmaintained
third-party writer), the encoder is written out by hand and unit-tested against a reference
decoder in `tests/test_remote_write.py`:

    WriteRequest { repeated TimeSeries timeseries = 1; }
    TimeSeries   { repeated Label labels = 1; repeated Sample samples = 2; }
    Label        { string name = 1; string value = 2; }
    Sample       { double value = 1; int64 timestamp = 2; }   # timestamp in ms since epoch

Prometheus requires label names sorted and a `__name__` label; both are enforced here so a caller
cannot produce a payload the ingester rejects for ordering.
"""

from __future__ import annotations

import logging
import re
import struct
import time
from dataclasses import dataclass, field

import cramjam
import httpx

log = logging.getLogger("slo_harness.remote_write")

LABEL_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


# --- protobuf wire encoding -------------------------------------------------------------------


def _varint(n: int) -> bytes:
    if n < 0:
        raise ValueError("varint encoder only handles non-negative values")
    out = bytearray()
    while True:
        chunk = n & 0x7F
        n >>= 7
        if n:
            out.append(chunk | 0x80)
        else:
            out.append(chunk)
            return bytes(out)


def _tag(field_number: int, wire_type: int) -> bytes:
    return _varint((field_number << 3) | wire_type)


def _len_delimited(field_number: int, payload: bytes) -> bytes:
    return _tag(field_number, 2) + _varint(len(payload)) + payload


def _encode_label(name: str, value: str) -> bytes:
    return _len_delimited(1, name.encode()) + _len_delimited(2, value.encode())


def _encode_sample(value: float, timestamp_ms: int) -> bytes:
    return _tag(1, 1) + struct.pack("<d", value) + _tag(2, 0) + _varint(timestamp_ms)


@dataclass(frozen=True)
class TimeSeries:
    """One metric series: a `__name__`, its labels, and one or more (value, ts_ms) samples."""

    name: str
    labels: dict[str, str]
    samples: list[tuple[float, int]] = field(default_factory=list)

    def encode(self) -> bytes:
        merged = {"__name__": self.name, **self.labels}
        for label in merged:
            if label != "__name__" and not LABEL_NAME_RE.match(label):
                raise ValueError(f"invalid label name: {label!r}")
        body = b"".join(
            _len_delimited(1, _encode_label(k, str(merged[k]))) for k in sorted(merged)
        )
        body += b"".join(
            _len_delimited(2, _encode_sample(v, ts))
            for v, ts in sorted(self.samples, key=lambda s: s[1])
        )
        return body


def encode_write_request(series: list[TimeSeries]) -> bytes:
    return b"".join(_len_delimited(1, ts.encode()) for ts in series)


# --- HTTP client ------------------------------------------------------------------------------


class RemoteWriteClient:
    """Pushes samples to a Prometheus remote_write endpoint (Grafana Cloud: /api/prom/push).

    Auth is HTTP basic: for Grafana Cloud the username is the metrics instance ID and the
    password is a Cloud Access Policy token with `metrics:write`.
    """

    def __init__(self, url: str, username: str, password: str, timeout: float = 30.0) -> None:
        self._http = httpx.Client(
            timeout=timeout,
            auth=(username, password),
            headers={
                "Content-Encoding": "snappy",
                "Content-Type": "application/x-protobuf",
                "X-Prometheus-Remote-Write-Version": "0.1.0",
                "User-Agent": "slo-harness/0.1",
            },
        )
        self._url = url

    def push(self, series: list[TimeSeries], retries: int = 3) -> None:
        if not series:
            return
        payload = bytes(cramjam.snappy.compress_raw(encode_write_request(series)))
        last: Exception | None = None
        for attempt in range(retries):
            try:
                response = self._http.post(self._url, content=payload)
                if response.status_code in (429,) or response.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"retryable status {response.status_code}: {response.text[:200]}",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                log.info("pushed %d series to remote_write", len(series))
                return
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status is not None and 400 <= status < 500 and status != 429:
                    raise  # a 4xx (other than 429) will not get better by retrying
                last = exc
            except httpx.HTTPError as exc:
                last = exc
            time.sleep(2**attempt)
        raise RuntimeError(f"remote_write push failed after {retries} attempts: {last}")

    def close(self) -> None:
        self._http.close()
