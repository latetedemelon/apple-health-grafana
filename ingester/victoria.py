"""
Minimal VictoriaMetrics writer using the InfluxDB line protocol.

Part of the unified personal-health-data platform. Every series written
here is tagged with a ``provider`` label so that data from each source is
stored together in one VictoriaMetrics instance but stays independently
filterable -- the platform's "store separate, display together" model.

The writer batches points and POSTs them to ``${VICTORIA_METRICS_URL}/write``.
Timestamps are emitted in nanoseconds (the InfluxDB line-protocol default).
"""
import datetime as _dt
import time
from numbers import Number
from typing import Any, Dict, Iterable, List, Optional

import requests


def _escape_measurement(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace(",", "\\,").replace(" ", "\\ ")


def _escape_tag(value: str) -> str:
    # tag keys and tag values: escape commas, equals signs and spaces
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace(",", "\\,")
        .replace("=", "\\=")
        .replace(" ", "\\ ")
    )


def _format_field_value(value: Any) -> Optional[str]:
    """Render a Python value as an InfluxDB line-protocol field value.

    Numbers are emitted without the ``i`` suffix (VictoriaMetrics stores
    everything as float64). ``None`` and string values are skipped: VM is a
    metrics store and DROPS THE WHOLE LINE if it contains a string field, so
    string data must travel as tags, not fields.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Number):
        # avoid scientific notation surprises; ints stay clean
        if isinstance(value, int):
            return str(value)
        return repr(float(value))
    # non-numeric (string) field: skip so sibling numeric fields still write
    return None


def _to_nanoseconds(t: Any) -> int:
    if isinstance(t, _dt.datetime):
        return int(t.timestamp() * 1_000_000_000)
    if isinstance(t, Number):
        # heuristic: anything below ~1e12 is seconds, otherwise already ns
        return int(t * 1_000_000_000) if t < 1_000_000_000_000 else int(t)
    return int(time.time() * 1_000_000_000)


class VictoriaMetricsWriter:
    """Batches points and writes them to VictoriaMetrics via line protocol."""

    def __init__(
        self,
        base_url: str,
        provider: str,
        extra_tags: Optional[Dict[str, str]] = None,
        batch_size: int = 10_000,
        timeout: int = 60,
    ) -> None:
        base_url = base_url.rstrip("/")
        # tolerate callers passing a full /write or /api/... URL
        for suffix in ("/write", "/api/v1/import/prometheus", "/api/v1/import"):
            if base_url.endswith(suffix):
                base_url = base_url[: -len(suffix)]
                break
        self.base_url = base_url
        self.write_url = base_url + "/write"
        self.health_url = base_url + "/health"
        self.provider = provider
        self.extra_tags = extra_tags or {}
        self.batch_size = batch_size
        self.timeout = timeout
        self._batch: List[str] = []
        self.total = 0

    def wait_ready(self, retries: int = 60, delay: float = 1.0) -> bool:
        for _ in range(retries):
            try:
                resp = requests.get(self.health_url, timeout=5)
                if resp.ok:
                    print("victoriametrics is ready")
                    return True
            except requests.RequestException:
                pass
            print("waiting on victoriametrics to be ready..")
            time.sleep(delay)
        return False

    def _line(self, point: Dict[str, Any]) -> Optional[str]:
        tags = {"provider": self.provider}
        tags.update(self.extra_tags)
        tags.update(point.get("tags") or {})

        tag_str = ",".join(
            f"{_escape_tag(k)}={_escape_tag(v)}"
            for k, v in tags.items()
            if v is not None and v != ""
        )

        field_parts = []
        for key, raw in (point.get("fields") or {}).items():
            rendered = _format_field_value(raw)
            if rendered is not None:
                field_parts.append(f"{_escape_tag(key)}={rendered}")
        if not field_parts:
            return None

        measurement = _escape_measurement(point["measurement"])
        head = f"{measurement},{tag_str}" if tag_str else measurement
        return f"{head} {','.join(field_parts)} {_to_nanoseconds(point.get('time'))}"

    def add(self, points: Iterable[Dict[str, Any]]) -> None:
        for point in points:
            line = self._line(point)
            if line:
                self._batch.append(line)
        if len(self._batch) >= self.batch_size:
            self.flush()

    def flush(self) -> None:
        if not self._batch:
            return
        payload = "\n".join(self._batch).encode("utf-8")
        resp = requests.post(self.write_url, data=payload, timeout=self.timeout)
        resp.raise_for_status()
        self.total += len(self._batch)
        print(f"inserted {self.total} records")
        self._batch = []
