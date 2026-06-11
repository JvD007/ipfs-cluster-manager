"""Filter-logica om pins te selecteren voor bulk-unpin.

Filtercriteria:
- ``name_contains``: case-insensitive substring match op pin.name
- ``name_regex``: regex match op pin.name
- ``before`` / ``after``: timestamp (ISO-8601) op pin.timestamp
- ``metadata``: dict van key/value paren waaraan álle moeten matchen
  (waarden mogen ``*`` als wildcard bevatten, of beginnen met ``re:`` voor regex)
- ``status``: alleen pins met deze tracker-status (bv. ``pinned``, ``pin_error``)
- ``cids``: expliciete lijst van CIDs (override; andere filters worden genegeerd)

De ``timestamp`` en ``metadata`` velden komen uit het Cluster Pin-object
(``/allocations`` endpoint).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


def _parse_ts(value: Any) -> Optional[datetime]:
    """Parse een ISO-8601 timestamp robuust (Cluster geeft RFC3339 nanos)."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str):
        return None
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    # Trim ondersteunde nanoseconde-precisie tot microseconden voor stdlib
    if "." in s:
        head, _, tail = s.partition(".")
        # vind tz-suffix
        m = re.search(r"([+-]\d{2}:?\d{2})$", tail)
        tz = m.group(1) if m else ""
        frac = tail[: m.start()] if m else tail
        frac = frac[:6]  # microseconden
        s = f"{head}.{frac}{tz}"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _meta_value_matches(pattern: str, actual: Any) -> bool:
    if actual is None:
        return False
    actual_s = str(actual)
    if pattern.startswith("re:"):
        try:
            return re.search(pattern[3:], actual_s) is not None
        except re.error:
            return False
    if "*" in pattern:
        regex = "^" + re.escape(pattern).replace(r"\*", ".*") + "$"
        return re.match(regex, actual_s) is not None
    return pattern == actual_s


@dataclass
class PinFilter:
    name_contains: Optional[str] = None
    name_regex: Optional[str] = None
    before: Optional[str] = None  # ISO-8601
    after: Optional[str] = None
    metadata: dict[str, str] = field(default_factory=dict)
    status: Optional[str] = None
    cids: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "PinFilter":
        if not isinstance(data, dict):
            return cls()
        return cls(
            name_contains=data.get("name_contains") or None,
            name_regex=data.get("name_regex") or None,
            before=data.get("before") or None,
            after=data.get("after") or None,
            metadata=dict(data.get("metadata") or {}),
            status=data.get("status") or None,
            cids=list(data.get("cids") or []),
        )

    def matches(self, pin: dict) -> bool:
        """Bepaalt of een Pin-object voldoet aan dit filter."""
        # Expliciete CID-lijst — sterkste filter
        if self.cids:
            cid = _extract_cid(pin)
            return cid in set(self.cids)

        name = pin.get("name") or ""

        if self.name_contains and self.name_contains.lower() not in name.lower():
            return False
        if self.name_regex:
            try:
                if not re.search(self.name_regex, name):
                    return False
            except re.error:
                return False

        ts = _parse_ts(pin.get("timestamp"))
        if self.before:
            target = _parse_ts(self.before)
            if target and (ts is None or ts >= target):
                return False
        if self.after:
            target = _parse_ts(self.after)
            if target and (ts is None or ts <= target):
                return False

        if self.metadata:
            pin_meta = pin.get("metadata") or {}
            for k, expected in self.metadata.items():
                if not _meta_value_matches(expected, pin_meta.get(k)):
                    return False

        if self.status:
            # Pin objecten uit /pins hebben peer_map[*].status; uit /allocations
            # is er geen tracker-status. Negeer als status niet beschikbaar is.
            peer_map = pin.get("peer_map")
            if isinstance(peer_map, dict) and peer_map:
                statuses = {p.get("status") for p in peer_map.values() if isinstance(p, dict)}
                if self.status not in statuses:
                    return False

        return True


def _extract_cid(pin: dict) -> str:
    """Cluster geeft CID als {'/': 'Qm...'} of als string."""
    cid = pin.get("cid")
    if isinstance(cid, dict):
        return cid.get("/", "")
    return cid or ""


def filter_pins(pins: list[dict], pin_filter: PinFilter) -> list[dict]:
    return [p for p in pins if pin_filter.matches(p)]
