"""SIMULATED internal database client.

This stands in for the real internal third-party master data (SQL/Oracle/API).
It loads a JSON seed and answers lookups with a small artificial latency so the
dashboard shows realistic timing. Replace :class:`InternalDatabaseClient` with a
real adapter - the pipeline only depends on ``lookup()`` / ``lookup_many()``.

TODO(integration): swap ``_load_seed`` for a real driver (e.g. SQLAlchemy /
pyodbc) and delete the latency simulation.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ..config import InternalSettings
from ..logging_setup import get_logger
from ..models import InternalRecord

logger = get_logger(__name__)


class InternalDatabaseClient:
    source_name = "internal-master-data (simulated)"

    def __init__(self, settings: InternalSettings, seed_path: Path | None = None) -> None:
        self.settings = settings
        self.seed_path = Path(seed_path) if seed_path else Path(settings.seed_path)
        self._records: dict[str, InternalRecord] = {}
        self._loaded = False

    # ------------------------------------------------------------------ loading
    def load(self) -> int:
        """(Re)load the seed file. Missing file = empty database, not an error."""
        self._records.clear()
        if self.seed_path.exists():
            payload = json.loads(self.seed_path.read_text(encoding="utf-8"))
            rows = payload.get("records", payload if isinstance(payload, list) else [])
            for row in rows:
                try:
                    record = InternalRecord.model_validate(row)
                except Exception as exc:  # noqa: BLE001 - seed quality issue, keep going
                    logger.warning("Skipping invalid internal seed row %r: %s", row.get("code"), exc)
                    continue
                self._records[record.code.upper()] = record
        else:
            logger.warning("Internal seed file not found at %s - internal lookups will all miss.", self.seed_path)
        self._loaded = True
        logger.info("Internal database loaded: %d records from %s", len(self._records), self.seed_path)
        return len(self._records)

    def ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    @property
    def record_count(self) -> int:
        self.ensure_loaded()
        return len(self._records)

    # ------------------------------------------------------------------ lookups
    def lookup_sync(self, code: str) -> InternalRecord | None:
        self.ensure_loaded()
        return self._records.get(code.strip().upper())

    async def lookup(self, code: str) -> InternalRecord | None:
        if self.settings.latency_ms:
            await asyncio.sleep(self.settings.latency_ms / 1000)
        return self.lookup_sync(code)

    async def lookup_many(self, codes: list[str]) -> dict[str, InternalRecord | None]:
        return {code: await self.lookup(code) for code in codes}

    def all_codes(self) -> list[str]:
        self.ensure_loaded()
        return sorted(self._records)
