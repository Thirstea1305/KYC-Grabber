"""SIMULATED external registry / screening client.

Produces *deterministic synthetic* records (same code -> same payload) so demos
and tests are reproducible, while still exercising the parts of the pipeline that
matter in production: latency, jitter, transient failures, retries with backoff,
not-found responses, caching and bounded concurrency.

ALL DATA RETURNED BY THIS MODULE IS FABRICATED AND MARKED AS SUCH.

TODO(integration): replace ``_simulate`` with a real HTTP call, keep the retry /
backoff / cache / concurrency behaviour, and map the provider payload onto
:class:`~kyc_grabber.models.ExternalRecord`.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
from datetime import date, timedelta

from ..config import ExternalSettings
from ..logging_setup import get_logger
from ..models import ExternalRecord, utcnow

logger = get_logger(__name__)

REGISTRIES = [
    "Companies House (UK)",
    "Handelsregister (DE)",
    "Registro Mercantil (ES)",
    "KvK (NL)",
    "Registro Imprese (IT)",
    "OpenCorporates Mirror",
]

COUNTRY_POOL = ["GB", "DE", "NL", "ES", "IT", "FR", "SE", "PL", "IE", "BE"]

DIRECTOR_FIRST = ["A. Ruiz", "M. Kowalski", "L. van Dijk", "S. Bianchi", "K. Novak", "J. Ferreira",
                  "P. Hansen", "E. Lindqvist", "T. Okafor", "N. Haddad"]
DIRECTOR_ROLE = ["Director", "Managing Director", "Non-Executive Director", "Company Secretary"]

ENTITY_TYPES = ["Private Limited Company", "Public Limited Company", "GmbH", "B.V.", "S.L.", "S.p.A."]

ADVERSE_TEMPLATES = [
    "Regulatory fine for late filing ({year} - {registry})",
    "Local press coverage of a supplier dispute ({year})",
    "Named in a tax-authority inspection report ({year})",
]
SANCTIONS_LISTS = ["EU Consolidated", "OFAC SDN", "UK HMT", "UN Consolidated"]


class ExternalLookupError(RuntimeError):
    """Raised when the provider stays unavailable after all retries."""


class ExternalDatabaseClient:
    source_name = "simulated-registry (SYNTHETIC DEMO DATA)"

    def __init__(self, settings: ExternalSettings) -> None:
        self.settings = settings
        self._cache: dict[str, ExternalRecord | None] = {}
        self._semaphore = asyncio.Semaphore(max(1, settings.concurrency))
        self.calls = 0
        self.retries = 0
        self.transient_failures = 0
        self.cache_hits = 0

    # ------------------------------------------------------------------ public
    @property
    def stats(self) -> dict[str, int]:
        return {
            "calls": self.calls,
            "retries": self.retries,
            "transient_failures": self.transient_failures,
            "cache_hits": self.cache_hits,
            "cached_codes": len(self._cache),
        }

    def clear_cache(self) -> None:
        self._cache.clear()

    async def fetch(
        self,
        code: str,
        *,
        expected_name: str | None = None,
        expected_country: str | None = None,
        expected_entity_type: str | None = None,
        use_cache: bool = True,
    ) -> ExternalRecord | None:
        """Look a code up in the external registry.

        Returns ``None`` when the registry legitimately has no record, and raises
        :class:`ExternalLookupError` when the provider could not be reached.
        """
        key = code.strip().upper()
        if use_cache and key in self._cache:
            self.cache_hits += 1
            return self._cache[key]

        async with self._semaphore:
            record = await self._fetch_with_retries(
                key,
                expected_name=expected_name,
                expected_country=expected_country,
                expected_entity_type=expected_entity_type,
            )

        if use_cache:
            self._cache[key] = record
        return record

    # ----------------------------------------------------------------- interals
    async def _fetch_with_retries(
        self,
        code: str,
        *,
        expected_name: str | None,
        expected_country: str | None,
        expected_entity_type: str | None,
    ) -> ExternalRecord | None:
        attempts = max(1, self.settings.max_retries)
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            self.calls += 1
            try:
                return await self._simulate(
                    code,
                    expected_name=expected_name,
                    expected_country=expected_country,
                    expected_entity_type=expected_entity_type,
                )
            except ExternalLookupError as exc:
                last_error = exc
                self.transient_failures += 1
                if attempt == attempts:
                    break
                self.retries += 1
                backoff = self.settings.retry_backoff_seconds * (2 ** (attempt - 1))
                logger.warning("External lookup for %s failed (attempt %d/%d): %s - retrying in %.1fs",
                               code, attempt, attempts, exc, backoff)
                await asyncio.sleep(backoff)

        raise ExternalLookupError(f"External registry unavailable for {code}: {last_error}")

    async def _simulate(
        self,
        code: str,
        *,
        expected_name: str | None,
        expected_country: str | None,
        expected_entity_type: str | None,
    ) -> ExternalRecord | None:
        """Produce a deterministic synthetic payload for *code*."""
        settings = self.settings
        rng = self._rng_for(code)

        delay_ms = settings.latency_ms + rng.uniform(0, max(0, settings.jitter_ms))
        await asyncio.sleep(delay_ms / 1000)

        if rng.random() < settings.failure_rate:
            raise ExternalLookupError("simulated 503 from registry")

        if rng.random() < settings.not_found_rate:
            return None

        registry = rng.choice(REGISTRIES)
        mirror_expected = expected_name is not None and rng.random() < 0.85

        if mirror_expected:
            legal_name = expected_name or self._company_name(rng)
            country = (expected_country or rng.choice(COUNTRY_POOL)).upper()
            entity_type = expected_entity_type or rng.choice(ENTITY_TYPES)
        else:
            legal_name = self._company_name(rng)
            country = rng.choice(COUNTRY_POOL)
            entity_type = rng.choice(ENTITY_TYPES)

        incorporation = date.today() - timedelta(days=rng.randint(400, 9000))
        sanctions_listed = rng.random() < 0.06
        pep_match = rng.random() < 0.08
        adverse_count = rng.choices([0, 1, 2, 3], weights=[70, 18, 8, 4])[0]
        year = rng.randint(2015, 2024)

        return ExternalRecord(
            code=code,
            registry_name=registry,
            registration_number=f"{rng.choice('ABCDEFGH')}{rng.randint(1000000, 9999999)}",
            legal_name=legal_name,
            entity_type=entity_type,
            country=country,
            incorporation_date=incorporation.isoformat(),
            status="active" if rng.random() > 0.08 else "dissolved",
            registered_address=f"{rng.randint(1, 250)} {self._company_name(rng)} Street, {country}",
            directors=[
                f"{rng.choice(DIRECTOR_FIRST)} - {rng.choice(DIRECTOR_ROLE)}"
                for _ in range(rng.randint(2, 4))
            ],
            shareholders=[f"{self._company_name(rng)} Holdings ({rng.randint(25, 100)}%)"],
            sanctions_listed=sanctions_listed,
            sanctions_lists=rng.sample(SANCTIONS_LISTS, k=rng.randint(1, 2)) if sanctions_listed else [],
            pep_match=pep_match,
            adverse_media_count=adverse_count,
            adverse_media=[
                rng.choice(ADVERSE_TEMPLATES).format(year=year, registry=registry)
                for _ in range(adverse_count)
            ],
            source_url=f"https://simulated-registry.example.com/entity/{code}",
            fetched_at=utcnow(),
        )

    @staticmethod
    def _rng_for(code: str) -> random.Random:
        digest = hashlib.sha256(f"kyc-grabber::{code}".encode()).digest()
        return random.Random(int.from_bytes(digest[:8], "big"))

    @staticmethod
    def _company_name(rng: random.Random) -> str:
        prefixes = ["Aurora", "Meridian", "Northwind", "Vantage", "Lumen", "Kestrel", "Blue Harbor",
                    "Arbor", "Solstice", "Ironbridge", "Cobalt", "Verdant"]
        suffixes = ["Logistics", "Trading", "Systems", "Partners", "Industries", "Consulting",
                    "Capital", "Manufacturing", "Services", "Technologies"]
        return f"{rng.choice(prefixes)} {rng.choice(suffixes)}"
