"""Unit-tests voor de filter-logica.

Run met:
  pytest -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.filters import PinFilter, filter_pins  # noqa: E402


def _pin(cid: str, name: str = "", ts: str | None = None, meta: dict | None = None, allocs=None, peer_map=None):
    pin: dict = {"cid": cid, "name": name, "metadata": meta or {}, "allocations": allocs or []}
    if ts:
        pin["timestamp"] = ts
    if peer_map is not None:
        pin["peer_map"] = peer_map
    return pin


PINS = [
    _pin("QmA", "backup-2024-01", "2024-01-15T10:00:00Z", {"env": "prod", "tag": "backup"}),
    _pin("QmB", "backup-2024-02", "2024-02-15T10:00:00Z", {"env": "prod", "tag": "backup"}),
    _pin("QmC", "staging-test", "2025-06-01T10:00:00Z", {"env": "staging", "tag": "ephemeral"}),
    _pin("QmD", "model-weights-llama", "2025-05-20T10:00:00Z", {"env": "prod", "tag": "model"}),
    _pin("QmE", "old-data", "2023-01-01T00:00:00Z", {"env": "dev"}),
]


def test_name_contains():
    f = PinFilter(name_contains="backup")
    assert {p["cid"] for p in filter_pins(PINS, f)} == {"QmA", "QmB"}


def test_name_regex():
    f = PinFilter(name_regex=r"^staging-")
    assert {p["cid"] for p in filter_pins(PINS, f)} == {"QmC"}


def test_before():
    f = PinFilter(before="2024-06-01T00:00:00Z")
    assert {p["cid"] for p in filter_pins(PINS, f)} == {"QmA", "QmB", "QmE"}


def test_after():
    f = PinFilter(after="2025-01-01T00:00:00Z")
    assert {p["cid"] for p in filter_pins(PINS, f)} == {"QmC", "QmD"}


def test_before_and_after():
    f = PinFilter(after="2024-01-01T00:00:00Z", before="2025-01-01T00:00:00Z")
    assert {p["cid"] for p in filter_pins(PINS, f)} == {"QmA", "QmB"}


def test_metadata_exact():
    f = PinFilter(metadata={"env": "staging"})
    assert {p["cid"] for p in filter_pins(PINS, f)} == {"QmC"}


def test_metadata_wildcard():
    f = PinFilter(metadata={"tag": "back*"})
    assert {p["cid"] for p in filter_pins(PINS, f)} == {"QmA", "QmB"}


def test_metadata_regex():
    f = PinFilter(metadata={"tag": "re:^(model|ephemeral)$"})
    assert {p["cid"] for p in filter_pins(PINS, f)} == {"QmC", "QmD"}


def test_metadata_combined_must_all_match():
    f = PinFilter(metadata={"env": "prod", "tag": "model"})
    assert {p["cid"] for p in filter_pins(PINS, f)} == {"QmD"}


def test_cids_override():
    # Expliciete CID-lijst overschrijft andere filters
    f = PinFilter(cids=["QmA", "QmC"], name_contains="zou-genegeerd-moeten-worden")
    assert {p["cid"] for p in filter_pins(PINS, f)} == {"QmA", "QmC"}


def test_cid_object_form():
    # Cluster levert soms cid als {'/': 'Qm...'}
    pins = [{"cid": {"/": "QmZ"}, "name": "x", "metadata": {}, "timestamp": "2025-01-01T00:00:00Z"}]
    f = PinFilter(cids=["QmZ"])
    assert len(filter_pins(pins, f)) == 1


def test_status_filter():
    pins = [
        _pin("QmA", "x", peer_map={"p1": {"status": "pinned"}}),
        _pin("QmB", "y", peer_map={"p1": {"status": "pin_error"}}),
    ]
    f = PinFilter(status="pin_error")
    assert {p["cid"] for p in filter_pins(pins, f)} == {"QmB"}


def test_no_filter_matches_all():
    f = PinFilter()
    assert len(filter_pins(PINS, f)) == len(PINS)


def test_from_dict_empty_safe():
    assert isinstance(PinFilter.from_dict({}), PinFilter)
    assert isinstance(PinFilter.from_dict(None), PinFilter)  # type: ignore[arg-type]
