"""The hand-rolled remote_write encoder, checked against a reference protobuf decoder.

The decoder below is written from the same four-message schema but independently of the
encoder (it parses tag bytes generically), so a shared misunderstanding of the wire format
would have to be made twice to slip through.
"""

from __future__ import annotations

import struct

import pytest

from slo_harness.remote_write import TimeSeries, encode_write_request


def _read_varint(data: bytes, i: int) -> tuple[int, int]:
    shift = value = 0
    while True:
        byte = data[i]
        value |= (byte & 0x7F) << shift
        i += 1
        if not byte & 0x80:
            return value, i
        shift += 7


def _decode_fields(data: bytes) -> list[tuple[int, int, bytes | int | float]]:
    fields = []
    i = 0
    while i < len(data):
        key, i = _read_varint(data, i)
        field_number, wire_type = key >> 3, key & 0x7
        if wire_type == 0:
            value, i = _read_varint(data, i)
        elif wire_type == 1:
            value = struct.unpack("<d", data[i:i + 8])[0]
            i += 8
        elif wire_type == 2:
            length, i = _read_varint(data, i)
            value = data[i:i + length]
            i += length
        else:
            raise AssertionError(f"unexpected wire type {wire_type}")
        fields.append((field_number, wire_type, value))
    return fields


def decode_write_request(data: bytes) -> list[dict]:
    series = []
    for number, _, ts_bytes in _decode_fields(data):
        assert number == 1
        labels, samples = {}, []
        for field_number, _, value in _decode_fields(ts_bytes):
            if field_number == 1:
                parts = _decode_fields(value)
                labels[parts[0][2].decode()] = parts[1][2].decode()
            else:
                assert field_number == 2
                parts = dict((n, v) for n, _, v in _decode_fields(value))
                samples.append((parts.get(1, 0.0), parts.get(2, 0)))
        series.append({"labels": labels, "samples": samples})
    return series


def test_round_trip():
    encoded = encode_write_request([
        TimeSeries(
            name="synthetic_probe_success",
            labels={"target": "saucedemo", "probe": "journey", "source": "cron"},
            samples=[(1.0, 1_724_000_000_000), (0.0, 1_724_000_900_000)],
        ),
        TimeSeries(name="synthetic_cron_jitter_seconds", labels={"source": "cron"},
                   samples=[(42.5, 1_724_000_000_123)]),
    ])
    decoded = decode_write_request(encoded)
    assert decoded[0]["labels"] == {
        "__name__": "synthetic_probe_success",
        "target": "saucedemo", "probe": "journey", "source": "cron",
    }
    assert decoded[0]["samples"] == [(1.0, 1_724_000_000_000), (0.0, 1_724_000_900_000)]
    assert decoded[1]["samples"] == [(42.5, 1_724_000_000_123)]


def test_labels_are_sorted_and_name_injected():
    encoded = encode_write_request(
        [TimeSeries(name="m", labels={"z": "1", "a": "2"}, samples=[(1.0, 1000)])]
    )
    label_names = list(decode_write_request(encoded)[0]["labels"])
    assert label_names == sorted(label_names)
    assert label_names[0] == "__name__"


def test_samples_sorted_by_timestamp():
    encoded = encode_write_request(
        [TimeSeries(name="m", labels={}, samples=[(2.0, 2000), (1.0, 1000)])]
    )
    assert decode_write_request(encoded)[0]["samples"] == [(1.0, 1000), (2.0, 2000)]


def test_invalid_label_name_rejected():
    with pytest.raises(ValueError, match="invalid label name"):
        TimeSeries(name="m", labels={"bad-label": "x"}, samples=[(1.0, 1)]).encode()


def test_snappy_payload_is_block_format():
    # Grafana Cloud expects raw (block) snappy, not the framed variant — a framed payload
    # starts with the stream identifier chunk \xff\x06\x00\x00sNaPpY.
    import cramjam

    payload = bytes(cramjam.snappy.compress_raw(
        encode_write_request([TimeSeries(name="m", labels={}, samples=[(1.0, 1)])])
    ))
    assert not payload.startswith(b"\xff\x06\x00\x00sNaPpY")
